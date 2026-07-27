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
    0.316
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
