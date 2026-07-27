"""
Detect the local machine's hardware with low-level operating-system commands.

Module summary
--------------
To extrapolate a measured cost onto other hardware, you first have to know the
hardware you measured on. This module asks the operating system directly (sysctl
on macOS, ``/proc`` and ``lscpu``/``nvidia-smi`` on Linux, PowerShell on Windows)
for the CPU model, core count, memory, and any GPU, and maps what it finds to the
power tables in :mod:`.green_algorithms` so a measurement can be re-based onto a
different chip. Every probe is defensive: a missing tool or an unexpected format
yields ``None`` for that field rather than an error, because a partial hardware
picture is still useful and this must never crash a measurement.

Author
------
Project maintainers.
"""

from __future__ import annotations

import platform
import re
import subprocess
from dataclasses import asdict, dataclass

from .green_algorithms import GPU_TDP_W

# Every OS probe is bounded so a wedged system tool cannot hang a measurement.
_PROBE_TIMEOUT_SECONDS: float = 4.0


@dataclass(frozen=True, slots=True)
class HardwareProfile:
    """A best-effort description of the machine a workload ran on.

    Parameters
    ----------
    os : str
        Operating system name (``Darwin``, ``Linux``, ``Windows``).
    arch : str
        Machine architecture (``arm64``, ``x86_64``, ...).
    cpu_model : str or None
        Human-readable CPU model string, when the OS reports one.
    logical_cores : int or None
        Number of logical CPU cores.
    memory_gb : float or None
        Total physical memory in gigabytes.
    gpu_model : str or None
        First GPU model string found, or ``None`` when no discrete GPU is seen.
    cpu_power_key : str or None
        Key into :data:`~cost_running.infrastructure.green_algorithms.CPU_W_PER_CORE`
        that best matches the detected CPU, for estimating power when unmeasured.
    gpu_power_key : str or None
        Key into :data:`~cost_running.infrastructure.green_algorithms.GPU_TDP_W`
        that best matches the detected GPU.
    """

    os: str
    arch: str
    cpu_model: str | None
    logical_cores: int | None
    memory_gb: float | None
    gpu_model: str | None
    cpu_power_key: str | None
    gpu_power_key: str | None

    def to_dict(self) -> dict[str, object]:
        """Return the profile as a plain dict for serialising into a model."""
        return asdict(self)


def _run(args: list[str]) -> str | None:
    """Run a probe command and return its stdout, or ``None`` on any failure.

    Parameters
    ----------
    args : list of str
        The command and arguments to execute.

    Returns
    -------
    str or None
        Captured stdout stripped of whitespace, or ``None`` when the tool is
        missing, times out, or exits non-zero.
    """
    try:
        # A missing binary raises FileNotFoundError; a slow one hits the timeout.
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _match_cpu_key(cpu_model: str | None, arch: str) -> str | None:
    """Map a CPU model string to the closest key in the power table.

    Parameters
    ----------
    cpu_model : str or None
        The detected CPU model string.
    arch : str
        The machine architecture, used to recognise Apple silicon.

    Returns
    -------
    str or None
        A key into ``CPU_W_PER_CORE``, defaulting to a generic server or desktop
        entry when no specific chip matches.
    """
    # Apple silicon is identified by architecture as much as by name.
    if arch == "arm64" and (cpu_model is None or "apple" in cpu_model.lower()):
        return "apple-m-series"
    if cpu_model is None:
        return None
    lowered = cpu_model.lower()
    # Look for a known family in the model string.
    if "epyc 9" in lowered:
        return "epyc-9654"
    if "epyc" in lowered:
        return "epyc-7742"
    if "xeon" in lowered and "gold" in lowered:
        return "xeon-gold-6248"
    if "xeon" in lowered:
        return "xeon-platinum-8175"
    # Fall back to a generic entry so an estimate is still possible.
    return "default-desktop-cpu" if arch == "arm64" else "default-server-cpu"


def _match_gpu_key(gpu_model: str | None) -> str | None:
    """Map a GPU model string to the closest key in the TDP table.

    Parameters
    ----------
    gpu_model : str or None
        The detected GPU model string.

    Returns
    -------
    str or None
        A key into ``GPU_TDP_W`` when a known accelerator name appears, else
        ``None`` (a consumer or integrated GPU has no datacenter TDP entry).
    """
    if not gpu_model:
        return None
    upper = gpu_model.upper()
    # Match against the known datacenter accelerators, longest names first so
    # "A100-80GB" wins over "A100".
    for key in sorted(GPU_TDP_W, key=len, reverse=True):
        if key.upper() in upper:
            return key
    return None


def _detect_cpu_model() -> str | None:
    """Return the CPU model string using the OS-appropriate probe."""
    system = platform.system()
    if system == "Darwin":
        # macOS reports a clean brand string via sysctl.
        return _run(["sysctl", "-n", "machdep.cpu.brand_string"])
    if system == "Linux":
        # lscpu is the tidiest source; fall back to /proc/cpuinfo.
        lscpu = _run(["lscpu"])
        if lscpu:
            match = re.search(r"^Model name:\s*(.+)$", lscpu, re.MULTILINE)
            if match:
                return match.group(1).strip()
        try:
            with open("/proc/cpuinfo", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except OSError:
            return None
    # platform.processor is a weak but universal last resort.
    return platform.processor() or None


def _detect_memory_gb() -> float | None:
    """Return total physical memory in gigabytes, or ``None``."""
    system = platform.system()
    if system == "Darwin":
        raw = _run(["sysctl", "-n", "hw.memsize"])  # bytes
        if raw and raw.isdigit():
            return round(int(raw) / 1024**3, 1)
    if system == "Linux":
        try:
            with open("/proc/meminfo", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("MemTotal:"):
                        # MemTotal is reported in kibibytes.
                        kib = int(line.split()[1])
                        return round(kib / 1024**2, 1)
        except (OSError, ValueError, IndexError):
            return None
    return None


def _detect_gpu_model() -> str | None:
    """Return the first GPU model string found, or ``None``."""
    # nvidia-smi is the reliable source on any machine with an NVIDIA GPU.
    smi = _run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])
    if smi:
        # Take the first line; a multi-GPU node lists one per line.
        return smi.splitlines()[0].strip()
    if platform.system() == "Darwin":
        # Apple silicon integrates the GPU; report it descriptively.
        chip = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
        if chip and "apple" in chip.lower():
            return f"{chip} (integrated)"
    return None


def detect_local_hardware() -> HardwareProfile:
    """Detect the local machine's hardware, mapping it to the power tables.

    Returns
    -------
    HardwareProfile
        A best-effort profile. Any field the OS does not report is ``None``; the
        function never raises, so a measurement can always record what it could
        learn about its host.

    Examples
    --------
    >>> profile = detect_local_hardware()
    >>> profile.os in {"Darwin", "Linux", "Windows"}
    True
    """
    system = platform.system()
    arch = platform.machine()
    cpu_model = _detect_cpu_model()
    gpu_model = _detect_gpu_model()
    # os.cpu_count is portable and sufficient for the core count.
    logical_cores = None
    try:
        import os as _os

        logical_cores = _os.cpu_count()
    except Exception:  # pragma: no cover - cpu_count is effectively always present.
        logical_cores = None

    return HardwareProfile(
        os=system,
        arch=arch,
        cpu_model=cpu_model,
        logical_cores=logical_cores,
        memory_gb=_detect_memory_gb(),
        gpu_model=gpu_model,
        cpu_power_key=_match_cpu_key(cpu_model, arch),
        gpu_power_key=_match_gpu_key(gpu_model),
    )
