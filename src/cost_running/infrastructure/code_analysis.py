"""
Read a repository's structure to guide measurement, statically and with an LLM.

Module summary
--------------
Before the tool runs a slice of a repository to measure it, it needs to know two
things the source can answer: what kind of work this is (is it compute-bound, is
there a lot of it) and how to run a representative, bounded slice. This module
answers both by *reading the code*, in two complementary ways:

- **Static analysis** (always run, deterministic). Scans the files for the
  framework in use, for a runnable test suite, and for the configuration numbers
  that set the size of the work (a training run's ``max_iters`` or ``epochs``, a
  batch job's item count). Numbers come from here, never from the language model,
  because a number a model invents is exactly the promptware this project
  refuses. A statically extracted ``max_iters`` is a fact in the file; a model's
  guess at it is not.

- **LLM structural reading** (optional, local Ollama). Asks a local model to
  classify the *qualitative* shape of the work: compute-bound or I/O-bound, the
  dominant operation, a one-line rationale. This is the weakest evidence the tool
  carries and is labelled as such (``evidence_source`` records the exact model).
  When Ollama is not running, this step is skipped and the static reading stands
  on its own; the pipeline never depends on a model being present.

The split is the honesty rule in code form: the deterministic reader owns the
quantities, the model only ever offers a classification, and every field says
where it came from.

Author
------
Project maintainers.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import ollama

# Files worth showing a model, and worth scanning statically, in priority order.
# Kept small: a structural read does not need the whole tree, and a local model's
# context is finite.
_INTEREST_NAMES: tuple[str, ...] = (
    "readme.md",
    "readme.rst",
    "train.py",
    "main.py",
    "run.py",
    "benchmark.py",
    "config.py",
    "config.yaml",
    "config.json",
    "pyproject.toml",
    "setup.py",
)

# Framework import fingerprints. Presence signals the compute stack, which informs
# both the workload classification and whether a GPU is plausibly involved.
_FRAMEWORK_HINTS: dict[str, tuple[str, ...]] = {
    "pytorch": ("import torch", "from torch"),
    "tensorflow": ("import tensorflow", "from tensorflow"),
    "jax": ("import jax", "from jax"),
    "sklearn": ("import sklearn", "from sklearn"),
    "numpy": ("import numpy", "from numpy"),
}

# Configuration keys that set the size of the work. A hit gives an honest,
# file-sourced total for the completion extrapolation (whole = slice / fraction).
_WORK_SIZE_KEYS: tuple[str, ...] = (
    "max_iters",
    "max_steps",
    "num_train_epochs",
    "n_epochs",
    "epochs",
    "num_iterations",
    "total_steps",
)


@dataclass(slots=True)
class WorkSize:
    """A file-sourced estimate of how much work the whole run performs.

    Parameters
    ----------
    unit : str
        The unit of work found (for example ``max_iters`` or ``epochs``).
    value : float
        The number read from the source.
    source : str
        Where it was read, as ``path::key``, so the figure is reconstructible.
    """

    unit: str
    value: float
    source: str


@dataclass(slots=True)
class StructuralAnalysis:
    """What reading the code says about the shape of the work.

    Parameters
    ----------
    workload_kind : str
        A coarse label: ``training``, ``inference``, ``etl``, ``api``, ``cli``,
        or ``unknown``.
    frameworks : list of str
        Compute frameworks detected by import fingerprint.
    compute_bound : bool or None
        Whether the work is compute-bound. ``None`` when neither the static pass
        nor the model could tell.
    dominant_operation : str
        A short phrase for the main cost driver (for example ``matmul in the
        training loop``), or an empty string when unknown.
    total_work : WorkSize or None
        A file-sourced size of the whole run, or ``None`` when no size key was
        found. Always statically sourced, never model-invented.
    has_tests : bool
        Whether a runnable test suite was detected (a ``tests`` directory or
        ``test_*.py`` files), so the slice runner can prefer it.
    test_command : list of str or None
        A representative, bounded command to run as a slice, or ``None``.
    reasoning : str
        A one-line rationale, from the model when used, else from the static pass.
    evidence_source : str
        ``static`` when only the deterministic pass ran, or ``llm:<model-tag>``
        when a local model contributed the classification.
    confidence : str
        ``low`` by default: a structural read is a guide for measurement, not a
        measurement.
    """

    workload_kind: str = "unknown"
    frameworks: list[str] = field(default_factory=list)
    compute_bound: bool | None = None
    dominant_operation: str = ""
    total_work: WorkSize | None = None
    has_tests: bool = False
    test_command: list[str] | None = None
    reasoning: str = ""
    evidence_source: str = "static"
    confidence: str = "low"


def _read_interest_files(repo: Path, max_bytes: int = 12000) -> dict[str, str]:
    """Return the contents of the interesting files, truncated per file.

    Parameters
    ----------
    repo : pathlib.Path
        Repository root.
    max_bytes : int, optional
        Per-file byte cap, so one large file cannot dominate.

    Returns
    -------
    dict
        Mapping of relative path to (truncated) text, for the files that exist.
    """
    found: dict[str, str] = {}
    # Case-insensitive match on basename against the interest list.
    wanted = {name.lower() for name in _INTEREST_NAMES}
    for path in sorted(repo.rglob("*")):
        if not path.is_file():
            continue
        if path.name.lower() not in wanted:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(path.relative_to(repo))
        found[rel] = text[:max_bytes]
        if len(found) >= 12:
            break
    return found


def _detect_frameworks(files: dict[str, str]) -> list[str]:
    """Return the compute frameworks whose import fingerprint appears in the files."""
    blob = "\n".join(files.values())
    return [name for name, hints in _FRAMEWORK_HINTS.items() if any(h in blob for h in hints)]


def _extract_work_size(repo: Path) -> WorkSize | None:
    """Return a file-sourced work size from a config assignment, or ``None``.

    Scans the interesting files for an assignment like ``max_iters = 600000`` and
    returns the first hit. The number is read verbatim from the file, so it is a
    fact with a source, not an estimate.

    Parameters
    ----------
    repo : pathlib.Path
        Repository root.

    Returns
    -------
    WorkSize or None
        The first work-size assignment found, or ``None``.
    """
    files = _read_interest_files(repo)
    for key in _WORK_SIZE_KEYS:
        # Match `key = 12345`, `key: 12345`, `"key": 12345`, tolerating quotes.
        pattern = re.compile(
            rf'["\']?{re.escape(key)}["\']?\s*[=:]\s*([0-9][0-9_]*)',
            re.IGNORECASE,
        )
        for rel, text in files.items():
            match = pattern.search(text)
            if match:
                value = float(match.group(1).replace("_", ""))
                if value > 0:
                    return WorkSize(unit=key, value=value, source=f"{rel}::{key}")
    return None


def _detect_tests(repo: Path) -> tuple[bool, list[str] | None]:
    """Return whether tests exist and a bounded command to run a slice of them.

    A repository's own test suite is the safest, most representative thing to
    execute: it is written to be run, and it exercises the real code paths. The
    command caps collection time so the slice stays short.

    Parameters
    ----------
    repo : pathlib.Path
        Repository root.

    Returns
    -------
    tuple
        ``(has_tests, command)`` where ``command`` is ``None`` when no test suite
        was found.
    """
    has_tests = (repo / "tests").is_dir() or any(repo.rglob("test_*.py"))
    if not has_tests:
        return False, None
    # A short, bounded slice: `-q -x` stops after the first failure and keeps the
    # slice small; capping wall-clock is left to the runner. sys.executable is the
    # running interpreter, so the command is identical on macOS, Ubuntu, and
    # Windows (where a bare "python" or "python3" would be wrong or missing).
    return True, [sys.executable, "-m", "pytest", "-q", "-x"]


def _static_analysis(repo: Path) -> StructuralAnalysis:
    """Return a purely static structural reading of the repository.

    Parameters
    ----------
    repo : pathlib.Path
        Repository root.

    Returns
    -------
    StructuralAnalysis
        A deterministic reading with ``evidence_source == "static"``.
    """
    files = _read_interest_files(repo)
    frameworks = _detect_frameworks(files)
    work = _extract_work_size(repo)
    has_tests, test_cmd = _detect_tests(repo)

    # A work-size key like max_iters is the strongest static signal for training.
    kind = "unknown"
    if work is not None and work.unit in {
        "max_iters",
        "max_steps",
        "num_train_epochs",
        "n_epochs",
        "epochs",
    }:
        kind = "training"

    # A deep-learning framework plus a training loop is plausibly compute-bound;
    # otherwise the static pass declines to guess and leaves it None.
    compute_bound: bool | None = None
    if frameworks and set(frameworks) & {"pytorch", "tensorflow", "jax"}:
        compute_bound = True

    reasoning_bits = []
    if frameworks:
        reasoning_bits.append("frameworks: " + ", ".join(frameworks))
    if work is not None:
        reasoning_bits.append(f"work size {work.unit}={work.value:g} from {work.source}")
    if has_tests:
        reasoning_bits.append("test suite present")

    return StructuralAnalysis(
        workload_kind=kind,
        frameworks=frameworks,
        compute_bound=compute_bound,
        total_work=work,
        has_tests=has_tests,
        test_command=test_cmd,
        reasoning="; ".join(reasoning_bits) or "no strong static signal",
        evidence_source="static",
    )


_LLM_PROMPT = """\
You are reading a code repository to classify the SHAPE of its main computation.
Do NOT estimate costs, prices, energy, or runtimes. Only classify structure.

Return ONLY a JSON object with exactly these fields:
- "workload_kind": one of "training", "inference", "etl", "api", "cli", "unknown"
- "compute_bound": true or false (is the dominant cost CPU/GPU compute, not I/O?)
- "dominant_operation": a short phrase, e.g. "matmul in the training loop"
- "reasoning": one sentence explaining the classification

Repository files (truncated):
{digest}
"""


def _llm_analysis(repo: Path, static: StructuralAnalysis) -> StructuralAnalysis | None:
    """Return an LLM-assisted reading layered on the static one, or ``None``.

    The model classifies the qualitative shape only; the statically sourced
    quantities (``total_work``, ``frameworks``, ``test_command``) are carried
    through unchanged, because those are facts read from files.

    Parameters
    ----------
    repo : pathlib.Path
        Repository root.
    static : StructuralAnalysis
        The static reading to layer the classification onto.

    Returns
    -------
    StructuralAnalysis or None
        A merged reading tagged ``llm:<model-tag>``, or ``None`` when Ollama is
        unavailable or its reply could not be parsed.
    """
    model = ollama.pick_model()
    if model is None:
        return None

    files = _read_interest_files(repo, max_bytes=4000)
    digest = "\n\n".join(f"--- {rel} ---\n{text}" for rel, text in files.items())
    reply = ollama.generate(_LLM_PROMPT.format(digest=digest[:16000]), model=model)
    if not reply:
        return None
    try:
        parsed = json.loads(reply)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None

    kind = str(parsed.get("workload_kind", static.workload_kind)) or static.workload_kind
    cb = parsed.get("compute_bound")
    compute_bound = bool(cb) if isinstance(cb, bool) else static.compute_bound
    dominant = str(parsed.get("dominant_operation", "")) or static.dominant_operation
    reasoning = str(parsed.get("reasoning", "")) or static.reasoning

    # A statically sourced training signal is trusted over a vaguer model guess.
    if static.workload_kind != "unknown":
        kind = static.workload_kind

    return StructuralAnalysis(
        workload_kind=kind,
        frameworks=static.frameworks,
        compute_bound=compute_bound,
        dominant_operation=dominant,
        total_work=static.total_work,
        has_tests=static.has_tests,
        test_command=static.test_command,
        reasoning=reasoning,
        evidence_source=f"llm:{model}",
        confidence="low",
    )


def analyze(repo: str | Path, *, use_llm: bool = True) -> StructuralAnalysis:
    """Read a repository's structure, statically and (optionally) with a local LLM.

    Parameters
    ----------
    repo : str or pathlib.Path
        Repository root to read.
    use_llm : bool, optional
        When ``True`` (default), layer a local-Ollama classification on top of the
        static reading if a server is running. When ``False``, or when no server
        is running, the static reading is returned unchanged.

    Returns
    -------
    StructuralAnalysis
        The merged reading. Quantities are always statically sourced; only the
        qualitative classification may come from the model, and ``evidence_source``
        records which.

    Examples
    --------
    >>> import tempfile, pathlib
    >>> d = pathlib.Path(tempfile.mkdtemp())
    >>> _ = (d / "train.py").write_text("import torch")
    >>> _ = (d / "config.py").write_text("max_iters = 5000")
    >>> a = analyze(d, use_llm=False)
    >>> a.workload_kind, a.total_work.unit, int(a.total_work.value)
    ('training', 'max_iters', 5000)
    """
    repo_path = Path(repo)
    static = _static_analysis(repo_path)
    if not use_llm:
        return static
    merged = _llm_analysis(repo_path, static)
    return merged if merged is not None else static
