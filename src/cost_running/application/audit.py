"""
Audit a repository: scaffold a cost model from what the code is and calls.

Module summary
--------------
Audit turns the facts gathered by detection into a first-pass cost model. It picks
a canonical unit of work from the archetype, seeds the compute assumptions from
archetype defaults (clearly labelled estimated, never measured), and, for every
paid service the code calls, writes a pricing block prefilled with the provider's
pricing page and a consumption placeholder, so filling in the real number is a
short, guided step rather than a research project. The whole output is marked a
``scaffold``: it is a starting point a human reviews, not an audit in the strong
sense. Nothing here is presented as more certain than it is.

Author
------
Project maintainers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from ..domain.schema import SCHEMA_VERSION
from ..infrastructure import code_analysis as ca
from ..infrastructure import green_algorithms as ga
from ..infrastructure import registry
from ..infrastructure.detect import (
    ServiceHit,
    detect_archetype,
    detect_languages,
    detect_services,
)
from ..infrastructure.github import cloned_repo
from .execution import SliceResult, has_run_consent, run_slice
from .extrapolate import CompletionResult, extrapolate_gpu

# Per-archetype seeds: active power (W), runtime (s), and an instance label. These
# are starting points, honest as estimates and nothing more; a human replaces
# them with measured or sourced values.
_ARCHETYPE_DEFAULTS: dict[str, dict[str, Any]] = {
    "inference": {"power_w": 400, "runtime_s": 0.1, "instance": "single-A100-node"},
    "training": {"power_w": 400, "runtime_s": 3600.0, "instance": "single-A100-node"},
    "api-service": {"power_w": 100, "runtime_s": 0.1, "instance": "single-CPU-node"},
    "cli-tool": {"power_w": 50, "runtime_s": 1.0, "instance": "single-CPU-node"},
    "etl-pipeline": {"power_w": 250, "runtime_s": 60.0, "instance": "single-CPU-node"},
    "frontend": {"power_w": 100, "runtime_s": 60.0, "instance": "single-CPU-node"},
    "unknown": {"power_w": 100, "runtime_s": 1.0, "instance": "single-CPU-node"},
}

# The canonical unit of work suggested for each archetype.
_ARCHETYPE_UNIT: dict[str, str] = {
    "inference": "one inference on a representative input",
    "training": "one training run to convergence",
    "api-service": "one API request",
    "cli-tool": "one CLI invocation",
    "etl-pipeline": "one pipeline run over a representative batch",
    "frontend": "one production build",
    "unknown": "one run of the primary task",
}


@dataclass(slots=True)
class AuditResult:
    """The output of auditing a repository.

    Parameters
    ----------
    name : str
        The repository directory name.
    languages : dict
        Language to file count.
    archetype : str
        The inferred archetype.
    model : dict
        The scaffold cost model, ready to write as YAML.
    service_hits : list of ServiceHit
        Detected paid-service uses, with the code evidence that found them.
    """

    name: str
    languages: dict[str, int]
    archetype: str
    model: dict[str, Any]
    service_hits: list[ServiceHit] = field(default_factory=list)
    analysis: ca.StructuralAnalysis | None = None
    slice_result: SliceResult | None = None

    def report(self) -> dict[str, Any]:
        """Return a JSON-ready summary of the audit (not the model itself)."""
        summary: dict[str, Any] = {
            "name": self.name,
            "languages": self.languages,
            "archetype": self.archetype,
            "detected_services": sorted({hit.key for hit in self.service_hits}),
            "maturity": self.model.get("maturity"),
        }
        if self.analysis is not None:
            summary["analysis_evidence"] = self.analysis.evidence_source
        if self.slice_result is not None:
            summary["measured_slice_seconds"] = round(
                self.slice_result.measurement.duration_seconds, 3
            )
        return summary


def _pricing_block(service_key: str) -> dict[str, Any]:
    """Build a prefilled, source-pointed pricing block for a detected service.

    Parameters
    ----------
    service_key : str
        The catalog key of the detected service.

    Returns
    -------
    dict
        A pricing entry with the provider's pricing URL filled in and the price
        and consumption left as sourced TODOs, so completing it is a short step.
    """
    row = registry.service_catalog().get(service_key, {})
    today = date.today().isoformat()
    # The price and the per-unit consumption are the two things a human supplies;
    # everything around them (where to look, how it is metered) is prefilled.
    return {
        "name": row.get("name", service_key),
        "service_key": service_key,
        "price_per_unit": {
            "value": None,
            "unit": row.get("unit_hint", "USD per unit"),
            "status": "TODO",
            "source_url": row.get("pricing_source_url"),
            "retrieved_date": today,
        },
        "usage_per_canonical_unit": {
            "value": None,
            "unit": "units per canonical run",
            "status": "TODO",
        },
        "subtotal_usd": {"value": None, "status": "TODO"},
    }


def _analysis_block(analysis: ca.StructuralAnalysis) -> dict[str, Any]:
    """Return a plain-metadata block describing a structural reading.

    The block uses flat keys, never ``value`` nodes, so the honesty walk that
    counts quantities does not mistake a structural fact (a workload kind, a
    framework list) for a numeric estimate. The one number it carries, the total
    work size, is stored under ``total_work_value`` with its file source, because
    it is a fact read from the code, not a modelled quantity.

    Parameters
    ----------
    analysis : ca.StructuralAnalysis
        The reading to serialise.

    Returns
    -------
    dict
        A JSON/YAML-ready block for the model's ``analysis`` section.
    """
    block: dict[str, Any] = {
        "workload_kind": analysis.workload_kind,
        "frameworks": analysis.frameworks,
        "compute_bound": analysis.compute_bound,
        "dominant_operation": analysis.dominant_operation,
        "reasoning": analysis.reasoning,
        # The weakest evidence the model carries, named exactly (static or llm:tag).
        "evidence_source": analysis.evidence_source,
    }
    if analysis.total_work is not None:
        block["total_work_unit"] = analysis.total_work.unit
        block["total_work_value"] = analysis.total_work.value
        block["total_work_source"] = analysis.total_work.source
    return block


def _apply_slice_measurement(model: dict[str, Any], slice_result: SliceResult) -> None:
    """Fold a measured slice into the model, with honest labels.

    The measured average power replaces the archetype's guessed power (still
    labelled ``estimated``: even a RAPL reading is a system-package attribution,
    not a per-process measurement, so it is a tighter estimate, not a fact). When
    the slice covered a known fraction of the workload, the projected whole-run
    runtime and energy replace the archetype seeds, each carrying the projection's
    method. A raw ``measurement`` block records the slice and its hot path.

    Parameters
    ----------
    model : dict
        The scaffold model to enrich, mutated in place.
    slice_result : SliceResult
        The measured slice, optionally with a completion projection.
    """
    m = slice_result.measurement
    if m.average_power_watts is not None:
        model["assumptions"]["average_power_draw_watts"] = {
            "value": round(m.average_power_watts, 1),
            "unit": "W",
            "status": "estimated",
            "notes": (
                f"Measured on this machine via {m.power_source} during a "
                f"{m.duration_seconds:.1f}s slice; tighter than the archetype default."
            ),
        }

    completion = slice_result.completion
    # Only project the whole run when the slice itself succeeded; a failed run
    # (e.g. the entrypoint did not recognise --max_iters) measured power but
    # did not cover the claimed fraction of the work.
    if completion is not None and completion.applicable and m.workload_exit_code == 0:
        model["scenario"]["runtime_seconds"] = {
            "value": round(completion.runtime_seconds, 3),
            "unit": "s",
            "status": "estimated",
            "notes": completion.method,
        }
        if completion.energy_kwh is not None:
            model["scenario"]["local_compute"]["energy_kwh"] = {
                "value": completion.energy_kwh,
                "status": "estimated",
                "notes": "Projected from the measured slice; " + completion.method,
            }

    # A raw record of what ran, so the reader can see the evidence behind the number.
    model["measurement"] = {
        "command": m.command,
        "slice_seconds": round(m.duration_seconds, 3),
        "exit_code": m.workload_exit_code,
        "power_source": m.power_source,
        "fraction_completed": slice_result.fraction_completed,
        "hot_functions": [
            {"function": e.function, "cumulative_seconds": e.cumulative_seconds}
            for e in slice_result.profile
        ],
        "warnings": m.warnings,
    }


def _apply_gpu_extrapolation(
    model: dict[str, Any],
    completion: CompletionResult,
    source_gpu_key: str,
    target_gpu: str,
) -> None:
    """Fold a cloud-GPU extrapolation into the model as a ``cloud_scenario`` block.

    Parameters
    ----------
    model : dict
        The scaffold model, mutated in place.
    completion : CompletionResult
        The whole-run projection from the measured slice. Must be applicable.
    source_gpu_key : str
        The catalog key of the GPU the local measurement ran on.
    target_gpu : str
        The catalog key of the target cloud GPU (e.g. ``H100``).
    """
    result = extrapolate_gpu(
        source_gpu=source_gpu_key,
        target_gpu=target_gpu,
        source_runtime_seconds=completion.runtime_seconds,
    )
    if not result.applicable:
        model["cloud_scenario"] = {
            "target_gpu": target_gpu,
            "applicable": False,
            "reasons": result.reasons,
            "status": "estimated",
        }
        return
    model["cloud_scenario"] = {
        "target_gpu": target_gpu,
        "applicable": True,
        "runtime_seconds": {"value": round(result.runtime_seconds, 3), "status": "estimated"},
        "active_power_w": {"value": result.active_power_w, "status": "estimated"},
        "energy_kwh": {"value": result.energy_kwh, "status": "estimated"},
        "method": result.method,
        "assumptions": result.assumptions,
        "limits": result.limits,
    }


def _maybe_apply_gpu_extrapolation(
    model: dict[str, Any],
    slice_result: SliceResult,
    target_gpu: str,
) -> None:
    """Attempt the local→cloud extrapolation, recording why it is not applicable when blocked.

    Requires a whole-run completion projection (i.e. the slice ran with a known
    ``fraction_completed``) and a datacenter GPU on the local machine to act as
    the source. Records a ``cloud_scenario`` block either way.

    Parameters
    ----------
    model : dict
        The scaffold model, mutated in place.
    slice_result : SliceResult
        The measured slice (may or may not have a completion projection).
    target_gpu : str
        The catalog key of the target cloud GPU (e.g. ``H100``).
    """
    from ..infrastructure.hardware import detect_local_hardware

    completion = slice_result.completion
    if completion is None or not completion.applicable:
        model["cloud_scenario"] = {
            "target_gpu": target_gpu,
            "applicable": False,
            "reasons": [
                "No whole-run projection available: the slice had no known "
                "fraction_completed (no capped entrypoint was run)."
            ],
            "status": "estimated",
        }
        return

    hw = detect_local_hardware()
    if hw.gpu_power_key is None:
        model["cloud_scenario"] = {
            "target_gpu": target_gpu,
            "applicable": False,
            "reasons": [
                "No datacenter GPU detected on the source machine; "
                "cannot re-base the runtime by throughput ratio."
            ],
            "status": "estimated",
        }
        return

    _apply_gpu_extrapolation(model, completion, hw.gpu_power_key, target_gpu)


def audit_repo(
    path: str | Path,
    *,
    run: bool = False,
    use_llm: bool = True,
    timeout: float = 120.0,
    target_gpu: str | None = None,
) -> AuditResult:
    """Audit a local repository and produce a scaffold cost model.

    Parameters
    ----------
    path : str or pathlib.Path
        The repository root to scan.
    run : bool, optional
        When ``True``, run a bounded slice of the repository (its own tests by
        default) to measure real power on this machine, folding the result into
        the model. Requires prior consent (see
        :func:`.execution.require_run_consent`); without it, running is skipped
        and a note is recorded rather than raising, so a static audit still
        completes.
    use_llm : bool, optional
        When ``True`` (default), let a local Ollama model classify the workload
        shape on top of the deterministic static reading. Skipped automatically
        when no server is running.
    timeout : float, optional
        Wall-clock cap for the measured slice, in seconds.
    target_gpu : str or None, optional
        When given (e.g. ``"H100"``), add a ``cloud_scenario`` block that
        extrapolates the projected whole-run cost onto that GPU. Requires
        ``run=True``, a capped entrypoint slice (so a ``fraction_completed`` is
        known), and a datacenter GPU on the local machine; missing any of these
        records a not-applicable ``cloud_scenario`` with the reason.

    Returns
    -------
    AuditResult
        The languages, archetype, detected services, the structural reading, an
        optional measured slice, and the scaffold model.

    Raises
    ------
    NotADirectoryError
        If ``path`` is not a directory.
    """
    repo = Path(path)
    if not repo.is_dir():
        raise NotADirectoryError(f"{repo} is not a directory.")

    # Gather the facts.
    languages = detect_languages(repo)
    archetype = detect_archetype(repo, languages)
    service_hits = detect_services(repo)
    defaults = _ARCHETYPE_DEFAULTS[archetype]

    # Read the code's structure: deterministic static pass, plus a local-LLM
    # classification when one is available. Owns qualitative shape, not numbers.
    analysis = ca.analyze(repo, use_llm=use_llm)

    # Seed the energy estimate from the archetype power and runtime. Energy is
    # estimated because both inputs are; cost and carbon stay TODO because their
    # price and grid inputs are not sourced yet.
    estimate = ga.estimate(
        runtime_seconds=defaults["runtime_s"],
        active_power_w=defaults["power_w"],
        pue=ga.PUE_DEFAULTS["on-prem"],
        grid_gco2e_per_kwh=0.0,
    )

    model: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "project_name": repo.resolve().name,
        "date_updated": date.today().isoformat(),
        "maturity": "scaffold",
        "canonical_unit_of_work": {
            "name": _ARCHETYPE_UNIT[archetype],
            "description": f"Representative unit of work for the {archetype} archetype.",
            "status": "estimated",
        },
        "deployment": {
            "provider": "on-prem",
            "instance_type": defaults["instance"],
            "country": "unknown",  # never guessed; a human confirms it
        },
        "assumptions": {
            "average_power_draw_watts": {
                "value": defaults["power_w"],
                "unit": "W",
                "status": "estimated",
                "notes": f"Archetype default for {archetype}; measure to improve.",
            },
            "electricity_price_usd_per_kwh": {
                "value": None,
                "unit": "USD/kWh",
                "status": "TODO",
            },
            "grid_carbon_intensity_gco2e_per_kwh": {
                "value": None,
                "unit": "gCO2e/kWh",
                "status": "TODO",
            },
        },
        "scenario": {
            "name": "default",
            "runtime_seconds": {
                "value": defaults["runtime_s"],
                "unit": "s",
                "status": "estimated",
            },
            "local_compute": {
                "energy_kwh": {"value": estimate.energy_kwh, "status": "estimated"},
                "electricity_cost_usd": {"value": None, "status": "TODO"},
                "carbon_gco2e": {"value": None, "status": "TODO"},
            },
            "external_api_cost_usd": {"value": None, "status": "TODO"},
            "totals": {
                "total_cost_usd": {"value": None, "status": "TODO"},
                "total_carbon_gco2e": {"value": None, "status": "TODO"},
            },
        },
        "exclusions": [
            "Embodied hardware carbon (manufacturing) is not counted by default.",
            "Storage and network egress are excluded unless materially relevant.",
        ],
    }

    # The structural reading is part of the model, labelled with its evidence.
    model["analysis"] = _analysis_block(analysis)

    # One pricing block per distinct detected service, each pointing at the
    # provider's pricing page so filling the number in is guided.
    detected_keys = sorted({hit.key for hit in service_hits})
    if detected_keys:
        model["pricing"] = {"external_apis": [_pricing_block(key) for key in detected_keys]}

    # Optionally run a bounded slice to measure real power, folding it in. Consent
    # is the user's, taken earlier by the caller; without it, a static audit still
    # completes rather than failing, with a note explaining why nothing ran.
    slice_result: SliceResult | None = None
    if run:
        slice_result = _maybe_run_slice(repo, analysis, timeout, model)
        if slice_result is not None:
            _apply_slice_measurement(model, slice_result)
            if target_gpu:
                _maybe_apply_gpu_extrapolation(model, slice_result, target_gpu)

    return AuditResult(
        name=repo.resolve().name,
        languages=languages,
        archetype=archetype,
        model=model,
        service_hits=service_hits,
        analysis=analysis,
        slice_result=slice_result,
    )


def _maybe_run_slice(
    repo: Path,
    analysis: ca.StructuralAnalysis,
    timeout: float,
    model: dict[str, Any],
) -> SliceResult | None:
    """Run a measured slice when consent is granted and a command is known.

    Prefers a capped entrypoint slice when a work size was detected statically:
    running ``train.py --max_iters 600`` covers a known fraction (600/600000) of
    the workload, so :func:`extrapolate_to_completion` can project the full run
    honestly. Falls back to the repo's test suite when no entrypoint is found;
    in that case ``fraction_completed`` is ``None`` and no projection is made.

    Parameters
    ----------
    repo : pathlib.Path
        Repository root.
    analysis : ca.StructuralAnalysis
        The structural reading, used to pick the slice command.
    timeout : float
        Wall-clock cap for the slice.
    model : dict
        The model, annotated with a note when nothing could be run.

    Returns
    -------
    SliceResult or None
        The measured slice, or ``None`` when consent is absent or no runnable
        command was found (each case recorded as a note on the model).
    """
    if not has_run_consent():
        model["analysis"]["run_note"] = (
            "Slice not run: consent to execute repository code was not granted."
        )
        return None

    # First choice: a capped entrypoint with a statically known fraction. This is
    # the honest path: the fraction is file-sourced (e.g. 600 / 600000 max_iters),
    # not guessed, so extrapolate_to_completion can project the whole run.
    command: list[str] | None = None
    fraction_completed: float | None = None
    if analysis.total_work is not None:
        command, fraction_completed = ca.capped_entrypoint_command(repo, analysis.total_work)

    # Second choice: the repo's own test suite. Tests don't map to the workload's
    # total_work fraction, so we leave fraction_completed as None and skip the
    # completion projection.
    if command is None:
        command = analysis.test_command

    if command is None:
        model["analysis"]["run_note"] = (
            "Slice not run: no runnable entrypoint or test suite was detected."
        )
        return None

    return run_slice(
        repo, command, timeout=timeout, profile=True, fraction_completed=fraction_completed
    )


def audit_github_repo(
    ref: str,
    *,
    run: bool = False,
    use_llm: bool = True,
    timeout: float = 120.0,
    target_gpu: str | None = None,
) -> AuditResult:
    """Clone a public GitHub repository and audit it.

    Parameters
    ----------
    ref : str
        A GitHub HTTPS URL, ``github.com/owner/repo``, or bare ``owner/repo``
        slug.  The repository must be publicly accessible.
    run : bool, optional
        Run a measured slice of the clone, subject to consent. See
        :func:`audit_repo`.
    use_llm : bool, optional
        Let a local Ollama model classify the workload shape. See :func:`audit_repo`.
    timeout : float, optional
        Wall-clock cap for the measured slice, in seconds.
    target_gpu : str or None, optional
        Cloud GPU to extrapolate onto. See :func:`audit_repo`.

    Returns
    -------
    AuditResult
        Identical to :func:`audit_repo` but sourced from the remote repository.
        The clone is deleted automatically after analysis.

    Raises
    ------
    ValueError
        If ``ref`` cannot be parsed as a GitHub reference.
    RuntimeError
        If ``git clone`` fails (network error, private repo, typo, etc.).
    """
    from ..infrastructure.github import parse_github_ref

    owner, repo_name = parse_github_ref(ref)
    with cloned_repo(ref) as path:
        result = audit_repo(path, run=run, use_llm=use_llm, timeout=timeout, target_gpu=target_gpu)
    # Replace the temp-dir basename with the real repo name.
    result.name = repo_name
    result.model["project_name"] = repo_name
    return result
