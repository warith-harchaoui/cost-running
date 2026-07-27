"""
Operating-system power profilers.

Module summary
--------------
Measuring energy honestly means reading the machine, not guessing. This module
provides the low-level power probes: on Linux, the Intel RAPL energy counter in
sysfs, which reports accumulated energy in microjoules and needs no privileges
once its file is readable; the hook for macOS ``powermetrics`` and other tools
sits alongside it. Each probe returns ``None`` when the source is unavailable, so
the caller can fall back to an estimate and, crucially, know that it did. The
honesty rule is enforced upstream: power is only ever labelled ``measured`` when a
probe actually returned a number.

Author
------
Project maintainers.
"""

from __future__ import annotations

import glob

# The RAPL sysfs counter wraps at a maximum; a single short measurement never
# wraps, but reading the max lets a caller detect and discard a wrapped delta.
_RAPL_ENERGY_GLOB: str = "/sys/class/powercap/intel-rapl:*/energy_uj"


def read_rapl_energy_uj() -> int | None:
    """Read the total Intel RAPL package energy counter, in microjoules.

    Sums every RAPL package domain present so a multi-socket machine is counted
    whole. Returns ``None`` when RAPL is absent (non-Intel, macOS, Windows, or a
    container without the sysfs files) or unreadable (permissions).

    Returns
    -------
    int or None
        Accumulated energy in microjoules across all packages, or ``None``.

    Notes
    -----
    RAPL covers CPU package power, not a discrete GPU. On a GPU workload it
    undercounts; the caller documents that boundary in the model's notes.
    """
    paths = glob.glob(_RAPL_ENERGY_GLOB)
    if not paths:
        # No RAPL on this machine; the caller must estimate instead.
        return None
    total = 0
    read_any = False
    for path in paths:
        try:
            with open(path, encoding="utf-8") as handle:
                total += int(handle.read().strip())
                read_any = True
        except (OSError, ValueError):
            # A single unreadable domain should not void a readable one, but if
            # none read, we return None below.
            continue
    return total if read_any else None


def rapl_average_power_watts(
    energy_before_uj: int, energy_after_uj: int, seconds: float
) -> float | None:
    """Compute average package power from two RAPL readings and a duration.

    Parameters
    ----------
    energy_before_uj : int
        RAPL energy counter before the workload, in microjoules.
    energy_after_uj : int
        RAPL energy counter after the workload, in microjoules.
    seconds : float
        Wall-clock duration between the two readings.

    Returns
    -------
    float or None
        Average power in watts, or ``None`` when the duration is non-positive or
        the counter went backwards (a wrap or a reset), which must not be
        reported as a real measurement.

    Examples
    --------
    >>> rapl_average_power_watts(1_000_000, 6_000_000, 0.5)
    10.0
    """
    # A non-positive interval cannot yield a rate.
    if seconds <= 0:
        return None
    delta_uj = energy_after_uj - energy_before_uj
    # A negative delta means the counter wrapped or reset; refuse to guess.
    if delta_uj < 0:
        return None
    # microjoules / 1e6 = joules; joules / seconds = watts.
    return (delta_uj / 1_000_000.0) / seconds
