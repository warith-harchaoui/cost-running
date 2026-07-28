"""
Extrapolate a measured cost onto other hardware, with a stated model.

Module summary
--------------
A measurement on one machine answers "what did this cost here". A common next
question is "what would it cost on an A100, or an H100, or in a hyperscale
datacenter". Answering that naively, by swapping the power number and keeping the
runtime, is wrong: a faster accelerator finishes the same work sooner, so both
the power *and* the runtime change. This module extrapolates with an explicit
performance model and refuses to answer outside the narrow regime where that
model holds.

The model (compute-bound, accelerator to accelerator)
-----------------------------------------------------
The work is a fixed number of floating-point operations. On a chip with peak
throughput ``P`` running at achieved utilisation ``u``, the time is
``work / (P * u)``. Holding the achieved utilisation equal across chips (the
central assumption), the runtime re-bases by the ratio of peak throughputs:

    runtime_target = runtime_source * (peak_source / peak_target)

Power re-bases to the target device power, and energy follows from the two:

    energy_target = power_target * runtime_target * PUE_target

This is a *bound with assumptions*, not a measurement, and every result says so.

Where it does not hold (and this module refuses)
------------------------------------------------
- Crossing CPU and GPU: throughput is not comparable; refuse.
- Memory-bound or I/O-bound work: runtime tracks bandwidth, not FLOP peak.
- Tiny runs dominated by launch and dispatch overhead.
- A precision the target does not support at the assumed rate.
- A workload that does not fit in the target's memory.

The caller is told which of these it cannot rule out, so the number is never
mistaken for a promise.

Completion extrapolation (a measured slice to the whole run)
------------------------------------------------------------
Some workloads are too long to run to the end just to price them: a full training
run converges in days. The honest move is to run a short, representative *slice*
on the real machine, measure it with the operating system's own counters (see
:mod:`.measure`), and project the whole run from that slice. If a measured slice
covered a known fraction ``f`` of the total work, the full-run cost is the slice
cost divided by ``f``:

    runtime_full = runtime_slice / f

Energy and carbon scale the same way, because they are the slice's power held over
the longer time. This is the first of two extrapolations the tool chains: a slice
projects to the whole run *on the same machine*, then :func:`extrapolate_gpu`
re-bases that whole-run figure *onto other hardware*. Both are estimates that
state their assumptions; neither is a measurement of the full run.

Author
------
Project maintainers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.taxonomy import ESTIMATED
from ..infrastructure import green_algorithms as ga


@dataclass(slots=True)
class ExtrapolationResult:
    """A cost extrapolated onto a target device, with its model and limits.

    Parameters
    ----------
    target : str
        The target hardware key (for example ``H100``).
    applicable : bool
        Whether the model applies. When ``False`` the numeric fields are ``None``
        and ``reasons`` explains why.
    confidence : str
        A coarse confidence: ``moderate`` inside the model's regime, ``low`` when
        an assumption is shaky, ``not-applicable`` when the model was refused.
    runtime_seconds : float or None
        Re-based runtime on the target, or ``None`` when not applicable.
    active_power_w : float or None
        Target device active power, or ``None``.
    energy_kwh : float or None
        Extrapolated energy, or ``None``.
    carbon_gco2e : float or None
        Extrapolated carbon when a grid intensity was given, else ``None``.
    status : str
        Always ``estimated``: an extrapolation is never a measurement.
    method : str
        A one-line statement of the formula used.
    assumptions : list of str
        The assumptions the number rests on.
    limits : list of str
        The conditions under which the number would be wrong.
    reasons : list of str
        When not applicable, why the model was refused.
    """

    target: str
    applicable: bool
    confidence: str
    runtime_seconds: float | None
    active_power_w: float | None
    energy_kwh: float | None
    carbon_gco2e: float | None
    status: str = ESTIMATED
    method: str = ""
    assumptions: list[str] = field(default_factory=list)
    limits: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


# The assumptions and limits are constants because they are properties of the
# model, not of a particular call; every applicable result carries them verbatim.
_MODEL_ASSUMPTIONS: tuple[str, ...] = (
    "The workload is compute-bound on both the source and the target device.",
    "Achieved utilisation (model FLOP utilisation) is equal on both devices.",
    "The target supports the same numeric precision at its rated throughput.",
    "The workload fits within the target device's memory.",
    "Launch and dispatch overhead is negligible next to the compute time.",
)
_MODEL_LIMITS: tuple[str, ...] = (
    "Memory-bound or I/O-bound work: runtime tracks bandwidth, not FLOP peak.",
    "Small runs dominated by kernel-launch or interpreter overhead.",
    "Different achieved utilisation between the two devices.",
    "Precision unsupported or run at a reduced rate on the target.",
)


def extrapolate_gpu(
    *,
    source_gpu: str,
    target_gpu: str,
    source_runtime_seconds: float,
    target_pue: float = ga.PUE_DEFAULTS["cloud-hyperscale"],
    grid_gco2e_per_kwh: float | None = None,
    source_usage_factor: float = 1.0,
) -> ExtrapolationResult:
    """Extrapolate a GPU measurement onto another GPU, compute-bound.

    Parameters
    ----------
    source_gpu : str
        The GPU the workload was measured on. Must be in the throughput table.
    target_gpu : str
        The GPU to extrapolate onto. Must be in both power and throughput tables.
    source_runtime_seconds : float
        Measured wall-clock runtime on the source GPU.
    target_pue : float, optional
        Datacenter overhead assumed for the target. Defaults to hyperscale.
    grid_gco2e_per_kwh : float or None, optional
        Grid intensity for the target's carbon. ``None`` skips carbon rather than
        inventing an intensity.
    source_usage_factor : float, optional
        The source device's active fraction; assumed to carry to the target.

    Returns
    -------
    ExtrapolationResult
        An applicable result inside the model's regime, or a refused result
        (``applicable=False``) with reasons when a required table entry is missing.

    Examples
    --------
    >>> r = extrapolate_gpu(source_gpu="A100", target_gpu="H100", source_runtime_seconds=1.0)
    >>> r.applicable and round(r.runtime_seconds, 3)
    0.315
    """
    reasons: list[str] = []
    # The model needs a throughput for both devices to re-base the runtime.
    if source_gpu not in ga.GPU_PEAK_BF16_TFLOPS:
        reasons.append(f"No throughput figure for source GPU {source_gpu!r}.")
    if target_gpu not in ga.GPU_PEAK_BF16_TFLOPS:
        reasons.append(f"No throughput figure for target GPU {target_gpu!r}.")
    if target_gpu not in ga.GPU_TDP_W:
        reasons.append(f"No power figure for target GPU {target_gpu!r}.")
    if reasons:
        # Refuse rather than guess: a missing table entry is out of the regime.
        return ExtrapolationResult(
            target=target_gpu,
            applicable=False,
            confidence="not-applicable",
            runtime_seconds=None,
            active_power_w=None,
            energy_kwh=None,
            carbon_gco2e=None,
            reasons=reasons,
        )

    # Re-base the runtime by the peak-throughput ratio (the central step).
    peak_source = ga.GPU_PEAK_BF16_TFLOPS[source_gpu]
    peak_target = ga.GPU_PEAK_BF16_TFLOPS[target_gpu]
    target_runtime = source_runtime_seconds * (peak_source / peak_target)

    # Power re-bases to the target device, keeping the source's active fraction.
    target_power = ga.GPU_TDP_W[target_gpu] * source_usage_factor

    # Energy and carbon follow from the re-based runtime and power.
    estimate = ga.estimate(
        runtime_seconds=target_runtime,
        active_power_w=target_power,
        pue=target_pue,
        grid_gco2e_per_kwh=grid_gco2e_per_kwh if grid_gco2e_per_kwh is not None else 0.0,
    )

    return ExtrapolationResult(
        target=target_gpu,
        applicable=True,
        confidence="moderate",
        runtime_seconds=target_runtime,
        active_power_w=target_power,
        energy_kwh=estimate.energy_kwh,
        carbon_gco2e=estimate.carbon_gco2e if grid_gco2e_per_kwh is not None else None,
        method=(
            f"runtime x peak({source_gpu})/peak({target_gpu}); "
            f"energy = TDP({target_gpu}) x runtime x PUE."
        ),
        assumptions=list(_MODEL_ASSUMPTIONS),
        limits=list(_MODEL_LIMITS),
    )


@dataclass(slots=True)
class CompletionResult:
    """A whole-run cost projected from a measured slice, with its model.

    Parameters
    ----------
    applicable : bool
        Whether the projection was made. ``False`` when the inputs are outside
        the model (a non-positive or greater-than-one fraction, a non-positive
        slice figure); the numeric fields are then ``None`` and ``reasons`` says
        why.
    fraction_completed : float
        The fraction of total work the measured slice covered, in ``(0, 1]``.
    scale : float or None
        The multiplier applied to the slice, ``1 / fraction_completed``.
    runtime_seconds : float or None
        Projected whole-run wall-clock time.
    energy_kwh : float or None
        Projected whole-run energy, when a slice energy was given.
    carbon_gco2e : float or None
        Projected whole-run carbon, when a slice carbon was given.
    cost_usd : float or None
        Projected whole-run electricity cost, when a slice cost was given.
    status : str
        Always ``estimated``: a projection from a slice is never a measurement of
        the whole run.
    method : str
        A one-line statement of the formula used.
    assumptions : list of str
        The assumptions the projection rests on.
    limits : list of str
        The conditions under which the projection would be wrong.
    reasons : list of str
        When not applicable, why the projection was refused.
    """

    applicable: bool
    fraction_completed: float
    scale: float | None
    runtime_seconds: float | None
    energy_kwh: float | None
    carbon_gco2e: float | None
    cost_usd: float | None
    status: str = ESTIMATED
    method: str = ""
    assumptions: list[str] = field(default_factory=list)
    limits: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


# The assumptions and limits of the slice-to-whole projection. Like the GPU
# model's, they are properties of the method, so every applicable result carries
# them verbatim.
_COMPLETION_ASSUMPTIONS: tuple[str, ...] = (
    "The measured slice is representative of the whole run's steady state.",
    "Per-unit work cost is constant across the run (no slow-down or speed-up).",
    "One-time warm-up (compilation, cache fill, data load) is either excluded "
    "from the slice or negligible next to the whole run.",
    "The fraction of work the slice covered is known, not itself guessed.",
)
_COMPLETION_LIMITS: tuple[str, ...] = (
    "Warm-up amortised over a short slice inflates the projection.",
    "Work whose per-unit cost changes over the run (growing batches, curriculum, "
    "checkpoints, evaluation passes).",
    "Non-linear phases: a final convergence tail or a one-off reduction step.",
    "A slice so short it is dominated by measurement noise.",
)


def extrapolate_to_completion(
    *,
    fraction_completed: float,
    slice_runtime_seconds: float,
    slice_energy_kwh: float | None = None,
    slice_carbon_gco2e: float | None = None,
    slice_cost_usd: float | None = None,
) -> CompletionResult:
    """Project a whole-run cost from a measured slice of known size.

    The first of the tool's two extrapolations: a short, measured slice on the
    real machine projects to the whole run on that same machine, before
    :func:`extrapolate_gpu` re-bases the whole run onto other hardware.

    Parameters
    ----------
    fraction_completed : float
        The fraction of the total work the measured slice covered, in ``(0, 1]``.
        For a training run this is ``iterations_measured / iterations_total``; for
        a batch job, ``items_processed / items_total``.
    slice_runtime_seconds : float
        Measured wall-clock time of the slice. Must be positive.
    slice_energy_kwh : float or None, optional
        Measured energy of the slice. ``None`` leaves the projected energy
        ``None`` rather than inventing one.
    slice_carbon_gco2e : float or None, optional
        Measured carbon of the slice, scaled the same way when given.
    slice_cost_usd : float or None, optional
        Measured electricity cost of the slice, scaled the same way when given.

    Returns
    -------
    CompletionResult
        An applicable projection when the inputs are inside the model, or a
        refused result (``applicable=False``) with reasons otherwise.

    Examples
    --------
    >>> r = extrapolate_to_completion(fraction_completed=0.01,
    ...                               slice_runtime_seconds=30.0,
    ...                               slice_energy_kwh=0.02)
    >>> r.applicable, round(r.runtime_seconds), round(r.energy_kwh, 3)
    (True, 3000, 2.0)
    """
    reasons: list[str] = []
    # A fraction outside (0, 1] cannot be inverted into an honest multiplier.
    if not 0.0 < fraction_completed <= 1.0:
        reasons.append(f"fraction_completed must be in (0, 1]; got {fraction_completed!r}.")
    # A non-positive slice runtime is not a measurement to scale from.
    if slice_runtime_seconds <= 0.0:
        reasons.append(f"slice_runtime_seconds must be positive; got {slice_runtime_seconds!r}.")
    if reasons:
        return CompletionResult(
            applicable=False,
            fraction_completed=fraction_completed,
            scale=None,
            runtime_seconds=None,
            energy_kwh=None,
            carbon_gco2e=None,
            cost_usd=None,
            reasons=reasons,
        )

    # The whole run is the slice divided by the fraction it covered.
    scale = 1.0 / fraction_completed
    return CompletionResult(
        applicable=True,
        fraction_completed=fraction_completed,
        scale=scale,
        runtime_seconds=slice_runtime_seconds * scale,
        energy_kwh=slice_energy_kwh * scale if slice_energy_kwh is not None else None,
        carbon_gco2e=slice_carbon_gco2e * scale if slice_carbon_gco2e is not None else None,
        cost_usd=slice_cost_usd * scale if slice_cost_usd is not None else None,
        method="whole = slice / fraction_completed (linear scaling of a representative slice).",
        assumptions=list(_COMPLETION_ASSUMPTIONS),
        limits=list(_COMPLETION_LIMITS),
    )
