"""
Energy and carbon estimation via the Green Algorithms method.

Module summary
--------------
When a value cannot be measured, it can be estimated from a published model. This
module implements the Green Algorithms formula (Lannelongue, Grealey, Inouye,
Advanced Science 2021, CC-BY 4.0): active power times runtime times datacenter
overhead gives energy, and energy times grid intensity gives carbon. It also
carries the hardware power tables the formula needs. Everything here is pure
arithmetic over numbers, so it is deterministic and trivially testable; the
honesty status of a *result* is decided by the caller, because an estimate built
on estimated inputs is itself estimated.

Author
------
Project maintainers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

# Memory draws roughly this many watts per gigabyte (DDR3/DDR4 average, Karyakin
# and Salem 2017). Small next to a GPU, but not zero for a large-memory node.
MEMORY_POWER_W_PER_GB: float = 0.3725

# Per-core CPU power (thermal design power divided by core count), in watts.
CPU_W_PER_CORE: dict[str, float] = {
    "xeon-platinum-8175": 10.0,  # 240 W / 24 cores
    "xeon-gold-6248": 7.5,  # 150 W / 20 cores
    "epyc-7742": 3.5,  # 225 W / 64 cores
    "epyc-9654": 3.75,  # 360 W / 96 cores
    "apple-m-series": 4.5,
    "default-server-cpu": 12.0,
    "default-desktop-cpu": 15.0,
}

# Whole-device GPU thermal design power (watts), vendor datasheet SXM/OAM figures
# at the default power cap. Kept current with mainstream accelerators.
GPU_TDP_W: dict[str, float] = {
    "T4": 70,
    "L4": 72,
    "L40S": 350,
    "P100": 250,
    "V100": 300,
    "A100": 400,
    "A100-40GB": 400,
    "A100-80GB": 400,
    "H100": 700,
    "H100-SXM": 700,
    "H200": 700,
    "B100": 700,  # NVIDIA Blackwell
    "B200": 1000,  # NVIDIA Blackwell
    "MI250X": 560,
    "MI300X": 750,
    "MI325X": 1000,  # AMD CDNA3
    "TPU-v4": 170,
}

# Peak dense BF16 throughput (TFLOP/s), vendor-rated. Used only to re-base the
# *runtime* of a compute-bound workload onto another accelerator: the same work
# (a fixed number of floating-point operations) takes less time on a faster chip.
# This is a throughput ratio, not a power number, and it is meaningful only for
# compute-bound work at comparable utilisation (see the extrapolation module).
GPU_PEAK_BF16_TFLOPS: dict[str, float] = {
    "V100": 125,
    "A100": 312,
    "A100-40GB": 312,
    "A100-80GB": 312,
    "H100": 989,
    "H100-SXM": 989,
    "H200": 989,
    "B100": 1800,  # NVIDIA Blackwell (dense BF16)
    "B200": 2250,  # NVIDIA Blackwell (dense BF16)
    "L4": 121,
    "L40S": 362,
    "MI300X": 1307,
    "MI325X": 1307,  # AMD CDNA3 (same compute die as MI300X)
    "TPU-v4": 275,
}

# Power usage effectiveness: the multiplier for datacenter overhead (cooling,
# power delivery). A laptop is close to 1; a hyperscale datacenter is efficient;
# an on-prem room is not. The world average is a conservative fallback.
PUE_DEFAULTS: dict[str, float] = {
    "cloud-hyperscale": 1.135,
    "cloud-typical": 1.20,
    "on-prem": 1.58,
    "local": 1.00,
    "global-average": 1.67,
}


@dataclass(frozen=True, slots=True)
class EstimateResult:
    """The output of one Green Algorithms estimate.

    Parameters
    ----------
    runtime_seconds : float
        Wall-clock time of one unit of work.
    active_power_w : float
        Total active power drawn by compute and memory, before overhead.
    pue : float
        Power usage effectiveness applied for datacenter overhead.
    energy_kwh : float
        Estimated energy per unit, in kilowatt-hours.
    grid_gco2e_per_kwh : float
        Grid carbon intensity used to convert energy to carbon.
    carbon_gco2e : float
        Estimated carbon per unit, in grams of CO2-equivalent.
    """

    runtime_seconds: float
    active_power_w: float
    pue: float
    energy_kwh: float
    grid_gco2e_per_kwh: float
    carbon_gco2e: float

    def to_dict(self) -> dict[str, float]:
        """Return the result as a plain dict with rounded figures.

        Returns
        -------
        dict of str to float
            The fields, with energy and carbon rounded to a readable precision.
        """
        data = asdict(self)
        # Round the derived figures so a report does not show spurious precision.
        for key in ("active_power_w", "energy_kwh", "carbon_gco2e"):
            data[key] = float(f"{data[key]:.6g}")
        return data


def active_power_watts(
    *,
    n_cores: int = 1,
    power_per_core_w: float | None = None,
    cpu: str | None = None,
    gpu: str | None = None,
    usage_factor: float = 1.0,
    memory_gb: float = 0.0,
) -> float:
    """Compute the active power draw of a compute configuration.

    Parameters
    ----------
    n_cores : int, optional
        Number of CPU cores, or GPU count when ``gpu`` is set. Defaults to 1.
    power_per_core_w : float or None, optional
        Explicit per-core (or per-device) power. Overrides ``cpu``/``gpu``.
    cpu : str or None, optional
        A key into :data:`CPU_W_PER_CORE` for the per-core power.
    gpu : str or None, optional
        A key into :data:`GPU_TDP_W` for the whole-device power.
    usage_factor : float, optional
        Fraction of the rated power actually drawn (0 to 1). Defaults to 1.0.
    memory_gb : float, optional
        Memory in gigabytes, added at :data:`MEMORY_POWER_W_PER_GB` each.

    Returns
    -------
    float
        Total active power in watts.

    Raises
    ------
    ValueError
        If a named ``cpu`` or ``gpu`` is unknown, or no power source is given.

    Examples
    --------
    >>> round(active_power_watts(gpu="A100", memory_gb=16), 2)
    405.96
    """
    # Resolve the per-unit power from the most specific source given.
    if power_per_core_w is not None:
        per_unit = power_per_core_w
    elif gpu is not None:
        if gpu not in GPU_TDP_W:
            raise ValueError(f"Unknown GPU {gpu!r}; known: {sorted(GPU_TDP_W)}.")
        per_unit = GPU_TDP_W[gpu]
    elif cpu is not None:
        if cpu not in CPU_W_PER_CORE:
            raise ValueError(f"Unknown CPU {cpu!r}; known: {sorted(CPU_W_PER_CORE)}.")
        per_unit = CPU_W_PER_CORE[cpu]
    else:
        raise ValueError("Provide one of power_per_core_w, cpu, or gpu.")

    # Compute power is device count times per-unit power times how hard it works;
    # memory adds a small constant per gigabyte.
    compute_power = n_cores * per_unit * usage_factor
    memory_power = memory_gb * MEMORY_POWER_W_PER_GB
    return compute_power + memory_power


def estimate(
    *,
    runtime_seconds: float,
    active_power_w: float,
    pue: float = PUE_DEFAULTS["global-average"],
    psf: float = 1.0,
    grid_gco2e_per_kwh: float = 475.0,
) -> EstimateResult:
    """Estimate energy and carbon for one unit of work.

    Parameters
    ----------
    runtime_seconds : float
        Wall-clock time of one unit of work.
    active_power_w : float
        Active power in watts (see :func:`active_power_watts`).
    pue : float, optional
        Datacenter overhead multiplier. Defaults to the world average.
    psf : float, optional
        Pragmatic scaling factor for repeated runs. Defaults to 1.0.
    grid_gco2e_per_kwh : float, optional
        Grid carbon intensity. Defaults to a world-average figure; a real model
        should source a region-specific value.

    Returns
    -------
    EstimateResult
        The energy and carbon estimate with its inputs.

    Examples
    --------
    >>> r = estimate(runtime_seconds=0.1, active_power_w=405.96, pue=1.135, grid_gco2e_per_kwh=55)
    >>> round(r.carbon_gco2e, 6)
    0.000704
    """
    # Energy (kWh) = power (W) * time (h) * overhead * scaling / 1000.
    runtime_hours = runtime_seconds / 3600.0
    energy_kwh = active_power_w * runtime_hours * pue * psf / 1000.0
    # Carbon (gCO2e) = energy (kWh) * grid intensity (gCO2e/kWh).
    carbon_gco2e = energy_kwh * grid_gco2e_per_kwh
    return EstimateResult(
        runtime_seconds=runtime_seconds,
        active_power_w=active_power_w,
        pue=pue,
        energy_kwh=energy_kwh,
        grid_gco2e_per_kwh=grid_gco2e_per_kwh,
        carbon_gco2e=carbon_gco2e,
    )
