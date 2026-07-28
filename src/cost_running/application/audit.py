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
from ..infrastructure import green_algorithms as ga
from ..infrastructure import registry
from ..infrastructure.detect import (
    ServiceHit,
    detect_archetype,
    detect_languages,
    detect_services,
)
from ..infrastructure.github import cloned_repo

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

    def report(self) -> dict[str, Any]:
        """Return a JSON-ready summary of the audit (not the model itself)."""
        return {
            "name": self.name,
            "languages": self.languages,
            "archetype": self.archetype,
            "detected_services": sorted({hit.key for hit in self.service_hits}),
            "maturity": self.model.get("maturity"),
        }


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


def audit_repo(path: str | Path) -> AuditResult:
    """Audit a local repository and produce a scaffold cost model.

    Parameters
    ----------
    path : str or pathlib.Path
        The repository root to scan.

    Returns
    -------
    AuditResult
        The languages, archetype, detected services, and the scaffold model.

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

    # One pricing block per distinct detected service, each pointing at the
    # provider's pricing page so filling the number in is guided.
    detected_keys = sorted({hit.key for hit in service_hits})
    if detected_keys:
        model["pricing"] = {"external_apis": [_pricing_block(key) for key in detected_keys]}

    return AuditResult(
        name=repo.resolve().name,
        languages=languages,
        archetype=archetype,
        model=model,
        service_hits=service_hits,
    )


def audit_github_repo(ref: str) -> AuditResult:
    """Clone a public GitHub repository and audit it.

    Parameters
    ----------
    ref : str
        A GitHub HTTPS URL, ``github.com/owner/repo``, or bare ``owner/repo``
        slug.  The repository must be publicly accessible.

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
        result = audit_repo(path)
    # Replace the temp-dir basename with the real repo name.
    result.name = repo_name
    result.model["project_name"] = repo_name
    return result
