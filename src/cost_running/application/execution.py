"""
Run a bounded slice of a repository, with the user's explicit consent, and profile it.

Module summary
--------------
Static reading and a language model can describe what code *would* cost; only
running it measures what it *does*. This module runs a short, bounded slice of a
repository on the real machine and measures it with the operating system's own
counters (see :mod:`.measure`), turning an archetype's estimated power into a
measured one and, when the slice covers a known fraction of a longer workload,
feeding the completion extrapolation (see :mod:`.extrapolate`).

Running third-party code is the user's decision, not the tool's
------------------------------------------------------------------
A cloned repository is untrusted code. This tool does not sandbox it: sandboxing
is not something the tool can promise honestly across macOS, Ubuntu, and Windows,
and pretending otherwise would be worse than being plain. Instead it asks the
user, once, for explicit consent, records the answer, and states plainly that
running the code is their responsibility. Without a recorded grant, the runner
refuses. The consent is a one-time gate, not a per-run nag.

Profiling
---------
The slice is measured two ways at once. The operating-system counters
(:func:`.measure.measure_command`: RAPL package energy, child CPU time and peak
memory) attribute the physical cost. For a Python command, an optional
``cProfile`` pass attributes wall time to the functions inside the slice, so the
report can name the hot path, not just the total. Both are cross-platform: the
counters degrade gracefully where a platform lacks them, and ``cProfile`` is in
the standard library everywhere.

Author
------
Project maintainers.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..infrastructure import green_algorithms as ga
from .extrapolate import CompletionResult, extrapolate_to_completion
from .measure import MeasurementResult, measure_command

# Where the one-time run consent is recorded. Kept next to the catalog overlay so
# a user's cost-running configuration lives in one place.
_CONSENT_FILENAME: str = "run_consent.json"


def _config_dir() -> Path:
    """Return the per-user config directory, honouring the registry override.

    Returns
    -------
    pathlib.Path
        ``COST_RUNNING_REGISTRY_DIR`` when set (so a checkout can keep consent
        local to the project), otherwise the platform user-config directory.
    """
    env = os.environ.get("COST_RUNNING_REGISTRY_DIR")
    if env:
        return Path(env)
    from platformdirs import user_config_dir

    return Path(user_config_dir("cost-running"))


def consent_path() -> Path:
    """Return the path of the recorded run-consent file (may not exist yet)."""
    return _config_dir() / _CONSENT_FILENAME


def has_run_consent() -> bool:
    """Return whether the user has already granted consent to run cloned code.

    Returns
    -------
    bool
        ``True`` only when a consent file exists and records a granted decision.
        Any read or parse problem is treated as "not granted": the safe default
        is to ask again, never to assume permission.
    """
    path = consent_path()
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return False
    return bool(isinstance(data, dict) and data.get("granted") is True)


def record_run_consent(granted: bool) -> Path:
    """Record the user's run-consent decision to disk.

    Parameters
    ----------
    granted : bool
        The decision to persist.

    Returns
    -------
    pathlib.Path
        The file the decision was written to.
    """
    path = consent_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump({"granted": bool(granted)}, handle)
    return path


_CONSENT_PROMPT = (
    "cost-running is about to EXECUTE code from a repository on this machine, "
    "with your user permissions and no sandbox.\n"
    "Running untrusted third-party code is your responsibility, not the tool's.\n"
    "Grant permission to run repository code from now on? [y/N] "
)


def require_run_consent(prompt=input) -> bool:
    """Return ``True`` if the user has (or now grants) consent to run cloned code.

    If consent was granted before, it is honoured silently. Otherwise the user is
    asked once, explicitly, with the responsibility stated plainly; the answer is
    recorded so the question is not repeated.

    Parameters
    ----------
    prompt : callable, optional
        A function taking a prompt string and returning the user's reply. Defaults
        to the builtin :func:`input`; injected in tests. When the reply cannot be
        read (a non-interactive session raising ``EOFError``), consent is refused.

    Returns
    -------
    bool
        Whether running is permitted.
    """
    if has_run_consent():
        return True
    try:
        reply = prompt(_CONSENT_PROMPT)
    except EOFError:
        # A non-interactive session cannot consent; refuse rather than assume.
        return False
    granted = str(reply).strip().lower() in {"y", "yes"}
    record_run_consent(granted)
    return granted


@dataclass(slots=True)
class ProfileEntry:
    """One hot function from the cProfile pass over a Python slice.

    Parameters
    ----------
    function : str
        ``file:line(name)`` identifying the function.
    cumulative_seconds : float
        Cumulative time spent in this function and its callees.
    calls : int
        Number of calls to this function during the slice.
    """

    function: str
    cumulative_seconds: float
    calls: int


@dataclass(slots=True)
class SliceResult:
    """What running and measuring a bounded slice found.

    Parameters
    ----------
    measurement : MeasurementResult
        The operating-system measurement of the slice (duration, power, energy).
    profile : list of ProfileEntry
        The hottest functions by cumulative time, when a cProfile pass ran.
    fraction_completed : float or None
        The fraction of a longer workload the slice covered, when known.
    completion : CompletionResult or None
        The whole-run projection from the slice, when a fraction was given.
    """

    measurement: MeasurementResult
    profile: list[ProfileEntry] = field(default_factory=list)
    fraction_completed: float | None = None
    completion: CompletionResult | None = None

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready summary of the slice run."""
        return {
            "measurement": self.measurement.to_dict(),
            "profile": [asdict(entry) for entry in self.profile],
            "fraction_completed": self.fraction_completed,
            "completion": asdict(self.completion) if self.completion is not None else None,
        }


def _is_python_command(command: list[str]) -> bool:
    """Return whether ``command`` invokes the Python interpreter directly."""
    if not command:
        return False
    head = Path(command[0]).name.lower()
    return head.startswith("python") or head.startswith("py")


def _wrap_with_cprofile(command: list[str], out_path: Path) -> list[str]:
    """Return ``command`` rewritten to run under cProfile, writing stats to disk.

    ``[python, -m, pytest, ...]`` becomes ``[python, -m, cProfile, -o, out, -m,
    pytest, ...]``. Only ``-m module`` forms are wrapped; a bare script path is
    left unwrapped (cProfile would need the script as its positional argument, a
    different rewrite) so the caller still measures it, just without the profile.

    Parameters
    ----------
    command : list of str
        The original Python command.
    out_path : pathlib.Path
        Where cProfile should write its binary stats.

    Returns
    -------
    list of str
        The rewritten command, or the original when it cannot be wrapped safely.
    """
    if "-m" in command:
        idx = command.index("-m")
        return [*command[:idx], "-m", "cProfile", "-o", str(out_path), *command[idx:]]
    return command


def _read_profile(out_path: Path, top: int = 8) -> list[ProfileEntry]:
    """Return the top functions by cumulative time from a cProfile stats file.

    Parameters
    ----------
    out_path : pathlib.Path
        The binary stats file cProfile wrote.
    top : int, optional
        How many hot functions to keep.

    Returns
    -------
    list of ProfileEntry
        The hottest functions, or an empty list when the file is missing or
        unreadable (the slice is still measured; the profile is a bonus).
    """
    if not out_path.exists():
        return []
    import pstats

    try:
        stats = pstats.Stats(str(out_path))
    except (OSError, ValueError, TypeError):
        return []
    stats.sort_stats("cumulative")

    entries: list[ProfileEntry] = []
    # pstats keeps its rows in .stats keyed by (file, line, func); the sorted
    # order lives in .fcn_list after sort_stats.
    ordered = getattr(stats, "fcn_list", None) or list(stats.stats.keys())
    for func in ordered[:top]:
        file_name, line, name = func
        # stats row: (call_count, num_calls, total_time, cumulative_time, callers)
        row = stats.stats[func]
        calls, cumulative = row[0], row[3]
        entries.append(
            ProfileEntry(
                function=f"{Path(file_name).name}:{line}({name})",
                cumulative_seconds=round(float(cumulative), 4),
                calls=int(calls),
            )
        )
    return entries


def run_slice(
    repo: str | Path,
    command: list[str],
    *,
    timeout: float | None = 120.0,
    fraction_completed: float | None = None,
    profile: bool = True,
    grid_gco2e_per_kwh: float | None = None,
    pue: float = ga.PUE_DEFAULTS["local"],
) -> SliceResult:
    """Run and measure a bounded slice of a repository.

    Parameters
    ----------
    repo : str or pathlib.Path
        Repository root; the command runs from here.
    command : list of str
        The command to run (for example the repo's own test suite).
    timeout : float or None, optional
        Hard wall-clock cap. A slice that overruns is measured as truncated.
    fraction_completed : float or None, optional
        The fraction of a longer workload this slice covered, in ``(0, 1]``. When
        given, the whole-run cost is projected with :func:`extrapolate_to_completion`.
    profile : bool, optional
        When ``True`` and the command is a Python ``-m`` invocation, run it under
        cProfile and attach the hot functions.
    grid_gco2e_per_kwh : float or None, optional
        Grid intensity for the slice's carbon figure.
    pue : float, optional
        Datacenter overhead for the slice's energy. Defaults to a local machine.

    Returns
    -------
    SliceResult
        The measurement, an optional profile, and an optional completion
        projection.

    Raises
    ------
    NotADirectoryError
        If ``repo`` is not a directory.
    RuntimeError
        If consent to run repository code has not been granted. Call
        :func:`require_run_consent` first; this is a hard gate, not a warning.
    """
    repo_path = Path(repo)
    if not repo_path.is_dir():
        raise NotADirectoryError(f"{repo_path} is not a directory.")
    if not has_run_consent():
        raise RuntimeError(
            "Running repository code requires explicit consent; call "
            "require_run_consent() first (it is the user's responsibility)."
        )

    run_command = list(command)
    profile_out: Path | None = None
    if profile and _is_python_command(command):
        # A temp file for cProfile's binary stats, cleaned up after reading.
        handle = tempfile.NamedTemporaryFile(suffix=".prof", delete=False)
        handle.close()
        profile_out = Path(handle.name)
        run_command = _wrap_with_cprofile(command, profile_out)

    measurement: MeasurementResult = measure_command(
        run_command,
        grid_gco2e_per_kwh=grid_gco2e_per_kwh,
        pue=pue,
        cwd=repo_path,
        timeout=timeout,
    )

    profile_entries: list[ProfileEntry] = []
    if profile_out is not None:
        profile_entries = _read_profile(profile_out)
        try:
            profile_out.unlink()
        except OSError:
            pass

    completion: CompletionResult | None = None
    if fraction_completed is not None:
        completion = extrapolate_to_completion(
            fraction_completed=fraction_completed,
            slice_runtime_seconds=measurement.duration_seconds,
            slice_energy_kwh=measurement.energy_kwh,
            slice_carbon_gco2e=measurement.carbon_gco2e,
        )

    return SliceResult(
        measurement=measurement,
        profile=profile_entries,
        fraction_completed=fraction_completed,
        completion=completion,
    )
