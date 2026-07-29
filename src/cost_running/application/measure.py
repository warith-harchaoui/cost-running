"""
Measure the real cost of running a command on this machine.

Module summary
--------------
This use case runs a command, times it, and reads what the operating system can
physically report: CPU time and peak memory from ``getrusage``, and package
energy from the Intel RAPL counter when it is available. From energy and wall
time it computes an average power, and it labels all energy and power figures
``estimated`` because even a real RAPL reading measures system-package energy
during the interval, not energy attributable exclusively to that process — the
attribution step is always an approximation. The ``power_source`` field records
where the number came from so consumers can distinguish a tight RAPL-based
estimate from a loose TDP-derived one. The result also records the local hardware
profile, so the measurement can later be extrapolated onto other chips (see
:mod:`.extrapolate`).

Energy attribution levels
--------------------------
``rapl_system_package``
    RAPL measured the CPU-package energy counter bracketing this run.
    Tight but system-level: idle processes, OS work, and device drivers running
    during the interval are included. Baseline subtraction is not applied.

``tdp_estimate``
    No RAPL counter was available. Power was estimated from the hardware TDP
    tables, scaled by a utilisation assumption, then multiplied by duration.

To obtain workload-attributed energy (system energy minus idle baseline) pass
``--repeat`` to collect multiple runs and subtract the idle baseline yourself,
or wait for the planned ``measure --baseline`` flag.

Author
------
Project maintainers.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..domain.taxonomy import ESTIMATED
from ..infrastructure import green_algorithms as ga
from ..infrastructure.hardware import HardwareProfile, detect_local_hardware
from ..infrastructure.profilers import rapl_average_power_watts, read_rapl_energy_uj

# What the energy figure was derived from. Carried in the result so a consumer
# can distinguish a tight RAPL-based estimate from a TDP model.
POWER_SOURCE_RAPL: str = "rapl_system_package"
POWER_SOURCE_ESTIMATED: str = "tdp_estimate"


@dataclass(slots=True)
class MeasurementResult:
    """What one measured run of a command cost.

    Parameters
    ----------
    command : str
        The command that was run, joined for display.
    duration_seconds : float
        Wall-clock runtime, always measured.
    workload_exit_code : int or None
        Return code of the workload. Non-zero signals a failed run; the energy
        figure reflects a failed execution and should not be reported as a
        successful-run cost.
    cpu_user_seconds : float or None
        User CPU time from ``getrusage``, when the platform supports it.
    cpu_system_seconds : float or None
        System CPU time from ``getrusage``.
    peak_memory_kb : int or None
        Peak resident set size, in kilobytes.
    average_power_watts : float or None
        Average package power: either a RAPL-based system estimate or a TDP model.
    power_source : str
        ``rapl_system_package`` when derived from the Intel RAPL counter,
        ``tdp_estimate`` when derived from hardware TDP tables.
    energy_kwh : float or None
        Energy for this run, from power and duration. Always ``estimated``.
    carbon_gco2e : float or None
        Carbon for this run, when a grid intensity was supplied.
    hardware : HardwareProfile
        The machine the command ran on.
    warnings : list of str
        Anything the caller should know (failed workload, GPU not counted, ...).
    """

    command: str
    duration_seconds: float
    workload_exit_code: int | None
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
            Always ``estimated``. Even when the power figure derives from a real
            RAPL counter reading, the attribution of system-package energy to this
            specific workload is an approximation (idle processes, OS work, and
            device drivers running during the interval are included). The
            ``power_source`` field records the tightness of the estimate:
            ``rapl_system_package`` is tighter than ``tdp_estimate``, but both
            are estimates of workload energy.
        """
        return ESTIMATED

    def to_dict(self) -> dict[str, object]:
        """Return the result as a JSON-ready dict, including the power status."""
        data = asdict(self)
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
        return None
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    # ru_maxrss is in bytes on macOS, kilobytes on Linux. Normalise to KB.
    try:
        import os_helper as _osh
        _macos = _osh.macos()
    except ImportError:
        import platform as _plat
        _macos = _plat.system() == "Darwin"
    peak_kb = usage.ru_maxrss // 1024 if _macos else usage.ru_maxrss
    return usage.ru_utime, usage.ru_stime, int(peak_kb)


def measure_command(
    command: list[str],
    *,
    grid_gco2e_per_kwh: float | None = None,
    pue: float = ga.PUE_DEFAULTS["local"],
    cwd: str | Path | None = None,
    timeout: float | None = None,
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
    cwd : str or pathlib.Path or None, optional
        Working directory to run the command in. Used to measure a slice of a
        cloned repository from its own root.
    timeout : float or None, optional
        Hard wall-clock cap in seconds. When the command overruns it, the process
        is killed and the run is measured as a truncated slice: the duration is
        the timeout, the exit code is ``None``, and a warning records the
        truncation. This is how a long workload (a full training run) is sampled
        without waiting for it to finish.

    Returns
    -------
    MeasurementResult
        Duration (measured), CPU time and peak memory when available, average
        power (estimated from RAPL or from TDP), energy, and optional carbon.
        Energy and power are always labelled ``estimated``; see the module
        docstring for the attribution distinction between ``rapl_system_package``
        and ``tdp_estimate``.

    Raises
    ------
    ValueError
        If ``command`` is empty.
    """
    if not command:
        raise ValueError("measure_command needs a non-empty command.")

    warnings: list[str] = []
    hardware = detect_local_hardware()

    energy_before = read_rapl_energy_uj()
    start = time.perf_counter()
    # A timeout kills the child and raises; we treat that as a truncated slice
    # rather than a failure, because sampling a long run is the intended use.
    timed_out = False
    returncode: int | None
    try:
        proc = subprocess.run(command, check=False, cwd=cwd, timeout=timeout)
        returncode = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        returncode = None
    duration = time.perf_counter() - start
    energy_after = read_rapl_energy_uj()

    if timed_out:
        warnings.append(
            f"Slice truncated at the {timeout:g}s timeout; measured only the sampled "
            "interval, not a completed run."
        )
    elif returncode != 0:
        warnings.append(
            f"Workload exited with code {returncode}; the energy figure "
            "reflects a failed run and may not represent typical execution cost."
        )

    rusage = _capture_child_rusage()
    cpu_user, cpu_system, peak_kb = rusage if rusage is not None else (None, None, None)

    average_power_watts: float | None
    power_source: str
    if energy_before is not None and energy_after is not None:
        average_power_watts = rapl_average_power_watts(energy_before, energy_after, duration)
        power_source = POWER_SOURCE_RAPL
        warnings.append(
            "RAPL reports system-package energy during the interval, not per-process "
            "attribution; idle and OS work during the run are included."
        )
        if hardware.gpu_power_key is not None:
            warnings.append("RAPL measures CPU package power only; a discrete GPU is not counted.")
        if average_power_watts is None:
            warnings.append("RAPL counter wrapped or reset; falling back to a TDP estimate.")
    else:
        average_power_watts = None
        power_source = POWER_SOURCE_ESTIMATED

    if average_power_watts is None:
        average_power_watts = _estimate_power_from_hardware(hardware, warnings)
        power_source = POWER_SOURCE_ESTIMATED

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
        workload_exit_code=returncode,
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
    if hardware.gpu_power_key is not None:
        warnings.append(f"Power estimated from {hardware.gpu_power_key} TDP (not measured).")
        return ga.GPU_TDP_W[hardware.gpu_power_key]
    if hardware.cpu_power_key is not None:
        cores = hardware.logical_cores or 1
        warnings.append(
            f"Power estimated from {hardware.cpu_power_key} at {cores} cores (not measured)."
        )
        return ga.CPU_W_PER_CORE[hardware.cpu_power_key] * cores * 0.5
    warnings.append("Hardware not recognised; power could not be estimated.")
    return None
