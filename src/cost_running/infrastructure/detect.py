"""
Scan a repository: languages, archetype, and the paid services it calls.

Module summary
--------------
Audit starts from what the code actually is. This module reads a repository tree
and reports three things deterministically: which languages it is written in (by
file extension), which archetype it most resembles (inference, training, a CLI, a
service, and so on), and which paid services it calls, matched against the
service catalog's detection hints. Service detection also captures the line of
code that matched, so a later contribution can carry that evidence into a pull
request. Nothing here estimates a cost; it gathers the facts an audit builds on.

Author
------
Project maintainers.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import registry

# File extension to language name. The list is intentionally broad so a mixed
# repository is described honestly rather than reduced to its dominant language.
_LANG_BY_EXT: dict[str, str] = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".c": "C",
    ".cc": "C++",
    ".cpp": "C++",
    ".cs": "C#",
    ".php": "PHP",
    ".rb": "Ruby",
    ".swift": "Swift",
    ".scala": "Scala",
    ".sh": "Bash",
    ".bash": "Bash",
}

# Directories that are never part of the code under study.
_SKIP_DIRS: frozenset[str] = frozenset(
    {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".mypy_cache"}
)

# Source extensions worth reading for service-detection hints.
_SOURCE_EXTS: frozenset[str] = frozenset(
    {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb"}
)

# Filenames that signal an archetype, checked in priority order below.
_INFERENCE_FILES: frozenset[str] = frozenset(
    {"inference.py", "predict.py", "serve.py", "infer.py", "predictor.py", "detect.py"}
)
_TRAINING_FILES: frozenset[str] = frozenset(
    {"train.py", "training.py", "finetune.py", "pretrain.py"}
)
_API_FILES: frozenset[str] = frozenset({"app.py", "server.py", "main.py", "index.ts", "index.js"})
_CLI_FILES: frozenset[str] = frozenset({"cli.py", "__main__.py", "cli.js", "cli.ts"})
_ETL_FILES: frozenset[str] = frozenset({"pipeline.py", "etl.py", "ingest.py", "dag.py"})

# A single source file larger than this is skipped when scanning for hints; a
# generated or vendored blob should not dominate detection or slow it down.
_MAX_SCAN_BYTES: int = 512_000


@dataclass(frozen=True, slots=True)
class ServiceHit:
    """One detected use of a paid service, with the evidence that found it.

    Parameters
    ----------
    key : str
        The service catalog key (for example ``openai``).
    path : str
        Repository-relative file the hint matched in.
    line : str
        The matching line of code, stripped, kept as evidence for a contribution.
    """

    key: str
    path: str
    line: str


def _iter_files(repo: Path):
    """Yield files under ``repo``, skipping vendored and build directories."""
    for path in repo.rglob("*"):
        # Skip anything inside a directory we never study.
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.is_file():
            yield path


def detect_languages(repo: Path) -> dict[str, int]:
    """Count source files per language.

    Parameters
    ----------
    repo : pathlib.Path
        The repository root.

    Returns
    -------
    dict
        Language name to file count, ordered most files first.
    """
    counts: dict[str, int] = {}
    for path in _iter_files(repo):
        language = _LANG_BY_EXT.get(path.suffix)
        if language:
            counts[language] = counts.get(language, 0) + 1
    # Present the dominant language first; a stable order helps diffs.
    return dict(sorted(counts.items(), key=lambda kv: kv[1], reverse=True))


def detect_archetype(repo: Path, languages: dict[str, int]) -> str:
    """Infer the repository's archetype from its filenames and languages.

    Parameters
    ----------
    repo : pathlib.Path
        The repository root.
    languages : dict
        The output of :func:`detect_languages`, used as a weak tie-breaker.

    Returns
    -------
    str
        One of ``inference``, ``training``, ``api-service``, ``cli-tool``,
        ``etl-pipeline``, ``frontend``, or ``unknown``.
    """
    names = {path.name for path in _iter_files(repo)}
    # Inference is checked before training: many training repos also serve.
    if names & _INFERENCE_FILES:
        return "inference"
    if names & _TRAINING_FILES:
        return "training"
    # A web service is signalled by an entrypoint plus a container.
    if names & _API_FILES and ("Dockerfile" in names or "package.json" in names):
        return "api-service"
    if names & _CLI_FILES:
        return "cli-tool"
    if names & _ETL_FILES:
        return "etl-pipeline"
    # A JS/TS project with a manifest and no server entrypoint reads as frontend.
    if "package.json" in names and ("JavaScript" in languages or "TypeScript" in languages):
        return "frontend"
    return "unknown"


def detect_services(repo: Path) -> list[ServiceHit]:
    """Find paid services the code calls, using the catalog's detection hints.

    Parameters
    ----------
    repo : pathlib.Path
        The repository root.

    Returns
    -------
    list of ServiceHit
        One hit per (service, file) pair, carrying the matching line as evidence.
        De-duplicated so a service imported in many files is not reported dozens
        of times, but the first match in each file is kept.
    """
    catalog = registry.service_catalog()
    # Precompute (service_key, hint) pairs once for the whole scan.
    hints = [(key, hint) for key, row in catalog.items() for hint in row.get("detect", [])]

    hits: list[ServiceHit] = []
    seen: set[tuple[str, str]] = set()
    for path in _iter_files(repo):
        if path.suffix not in _SOURCE_EXTS:
            continue
        try:
            if path.stat().st_size > _MAX_SCAN_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel = str(path.relative_to(repo))
        # Check each hint; record the first line in this file that matches it.
        for key, hint in hints:
            if (key, rel) in seen:
                continue
            if hint in text:
                line = next((ln.strip() for ln in text.splitlines() if hint in ln), hint)
                hits.append(ServiceHit(key=key, path=rel, line=line))
                seen.add((key, rel))
    return hits
