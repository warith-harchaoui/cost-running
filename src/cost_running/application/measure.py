"""
Measure the real cost of running a command on this machine.

Module summary
--------------
This use case runs a command, times it, and reads what the operating system can
physically report: CPU time and peak memory from ``getrusage``, and package
energy from the Intel RAPL counter when it is available. From energy and wall
time it computes an average power, and it labels that power ``measured`` only when
a counter actually produced it; otherwise it estimates power from the detected
hardware and says so. The result also records the local hardware profile, so the
measurement can later be extrapolated onto other chips (see
:mod:`.extrapolate`).

Author
------
Project maintainers.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import asdict, dataclass, field

from ..domain.taxonomy import ESTIMATED, MEASURED
from ..infrastructure import green_algorithms as ga
from ..infrastructure.hardware import HardwareProfile, detect_local_hardware
from ..infrastructure.profilers import rapl_average_power_watts, read_rapl_energy_uj

# Where a power number came from, which decides its honesty status.
POWER_SOURCE_RAPL: str = "rapl"
POWER_SOURCE_ESTIMATED: str = "estimated-from-tdp"


@dataclass(slots=True)
class MeasurementResult:
    """What one measured run of a command cost.

    Parameters
    ----------
    command : str
        The command that was run, joined for display.
    duration_seconds : float
        Wall-clock runtime, always measured.
    cpu_user_seconds : float or None
        User CPU time from ``getrusage``, when the platform supports it.
    cpu_system_seconds : float or None
        System CPU time from ``getrusage``.
    peak_memory_kb : int or None
        Peak resident set size, in kilobytes.
    average_power_watts : float or None
        Average package power, measured from RAPL or estimated from hardware.
    power_source : str
        ``rapl`` when measured, ``estimated-from-tdp`` when derived from the
        hardware TDP tables.
    energy_kwh : float or None
        Energy for this run, from power and duration.
    carbon_gco2e : float or None
        Carbon for this run, when a grid intensity was supplied.
    hardware : HardwareProfile
        The machine the command ran on.
    warnings : list of str
        Anything the caller should know (missing RAPL, GPU not counted, ...).
    """

    command: str
    duration_seconds: float
    cpu_user_seconds: float | None
    cpu_system_seconds: float | None
    peak_memory_kb: int | None
    average_power_watts: float | None
    power_source: str
    energy_kwh: float | None
    carbon_gco2e: float | None
    hardware: HardwareProfile
    warnings: list[str] = field(default_factory=list)

    @property
    def power_status(self) -> str:
        """Return the honesty status of the power figure.

        Returns
        -------
        str
            ``measured`` only when the power came from a real RAPL reading;
            ``estimated`` otherwise. This is the mechanical guard that stops a
            run from claiming a power it never measured.
        """
        if self.power_source == POWER_SOURCE_RAPL and self.average_power_watts is not None:
            return MEASURED
        return ESTIMATED

    def to_dict(self) -> dict[str, object]:
        """Return the result as a JSON-ready dict, including the power status."""
        data = asdict(self)
        # Surface the derived status so a consumer does not re-derive the rule.
        data["power_status"] = self.power_status
        return data


def _capture_child_rusage() -> tuple[float, float, int] | None:
    """Return (user_seconds, system_seconds, peak_kb) for child processes.

    Returns
    -------
    tuple or None
        The cumulative child resource usage, or ``None`` on a platform without
        ``resource`` (Windows) so the caller degrades gracefully.
    """
    try:
        import resource
    except ImportError:
        # Windows has no resource module; CPU time and peak memory are unavailable.
        return None
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    # ru_maxrss is bytes on macOS and kilobytes on Linux; normalise to kilobytes.
    import sys

    peak_kb = usage.ru_maxrss // 1024 if sys.platform == "darwin" else usage.ru_maxrss
    return usage.ru_utime, usage.ru_stime, int(peak_kb)


def measure_command(
    command: list[str],
    *,
    grid_gco2e_per_kwh: float | None = None,
    pue: float = ga.PUE_DEFAULTS["local"],
) -> MeasurementResult:
    """Run a command and measure its cost on this machine.

    Parameters
    ----------
    command : list of str
        The command and its arguments to execute.
    grid_gco2e_per_kwh : float or None, optional
        Grid carbon intensity for the carbon figure. Left ``None`` to skip carbon
        when no sourced intensity is available, rather than inventing one.
    pue : float, optional
        Datacenter overhead. Defaults to ``1.0`` (a local machine).

    Returns
    -------
    MeasurementResult
        Duration (measured), CPU time and peak memory when available, average
        power (measured from RAPL or estimated), energy, and optional carbon.

    Raises
    ------
    ValueError
        If ``command`` is empty.
    """
    if not command:
        raise ValueError("measure_command needs a non-empty command.")

    warnings: list[str] = []
    hardware = detect_local_hardware()

    # Read the energy counter immediately before and after so the delta brackets
    # the workload as tightly as possible.
    energy_before = read_rapl_energy_uj()
    start = time.perf_counter()
    # Run the command to completion, letting it inherit stdio so a user sees it.
    subprocess.run(command, check=False)
    duration = time.perf_counter() - start
    energy_after = read_rapl_energy_uj()

    # Resource usage of the child, when the platform reports it.
    rusage = _capture_child_rusage()
    cpu_user, cpu_system, peak_kb = rusage if rusage is not None else (None, None, None)

    # Power: prefer a real RAPL measurement; fall back to an honest estimate.
    average_power_watts: float | None
    power_source: str
    if energy_before is not None and energy_after is not None:
        average_power_watts = rapl_average_power_watts(energy_before, energy_after, duration)
        power_source = POWER_SOURCE_RAPL
        # RAPL sees the CPU package, not a discrete GPU; say so if a GPU is present.
        if hardware.gpu_power_key is not None:
            warnings.append("RAPL measures CPU package power only; a discrete GPU is not counted.")
        if average_power_watts is None:
            warnings.append("RAPL counter wrapped or reset; falling back to an estimate.")
    else:
        average_power_watts = None
        power_source = POWER_SOURCE_ESTIMATED

    # When power was not measured, estimate it from the detected hardware TDP so a
    # first-pass energy figure exists, clearly labelled estimated.
    if average_power_watts is None:
        average_power_watts = _estimate_power_from_hardware(hardware, warnings)
        power_source = POWER_SOURCE_ESTIMATED

    # Energy and carbon follow from power, duration, and overhead.
    energy_kwh: float | None = None
    carbon_gco2e: float | None = None
    if average_power_watts is not None:
        result = ga.estimate(
            runtime_seconds=duration,
            active_power_w=average_power_watts,
            pue=pue,
            grid_gco2e_per_kwh=grid_gco2e_per_kwh if grid_gco2e_per_kwh is not None else 0.0,
        )
        energy_kwh = result.energy_kwh
        carbon_gco2e = result.carbon_gco2e if grid_gco2e_per_kwh is not None else None

    return MeasurementResult(
        command=" ".join(command),
        duration_seconds=duration,
        cpu_user_seconds=cpu_user,
        cpu_system_seconds=cpu_system,
        peak_memory_kb=peak_kb,
        average_power_watts=average_power_watts,
        power_source=power_source,
        energy_kwh=energy_kwh,
        carbon_gco2e=carbon_gco2e,
        hardware=hardware,
        warnings=warnings,
    )


def _estimate_power_from_hardware(hardware: HardwareProfile, warnings: list[str]) -> float | None:
    """Estimate active power from a detected hardware profile.

    Parameters
    ----------
    hardware : HardwareProfile
        The detected local hardware.
    warnings : list of str
        Accumulator; a note is appended explaining the estimate's basis.

    Returns
    -------
    float or None
        An estimated active power in watts, or ``None`` when the hardware could
        not be mapped to any power table entry.
    """
    # A GPU dominates power when present, so prefer it.
    if hardware.gpu_power_key is not None:
        warnings.append(f"Power estimated from {hardware.gpu_power_key} TDP (not measured).")
        return ga.GPU_TDP_W[hardware.gpu_power_key]
    if hardware.cpu_power_key is not None:
        cores = hardware.logical_cores or 1
        warnings.append(
            f"Power estimated from {hardware.cpu_power_key} at {cores} cores (not measured)."
        )
        # A rough active estimate: a fraction of the cores are busy at once.
        return ga.CPU_W_PER_CORE[hardware.cpu_power_key] * cores * 0.5
    warnings.append("Hardware not recognised; power could not be estimated.")
    return None
