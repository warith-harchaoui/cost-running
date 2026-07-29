"""Tests for repository detection and the audit scaffold."""

from __future__ import annotations

from pathlib import Path

import pytest

from cost_running.application.audit import audit_repo
from cost_running.application.validate import validate_model
from cost_running.infrastructure.detect import (
    detect_archetype,
    detect_languages,
    detect_services,
)


def _make_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create a throwaway repository from a mapping of path to content."""
    for rel, content in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return tmp_path


def test_detect_languages_counts_by_extension(tmp_path):
    repo = _make_repo(tmp_path, {"a.py": "", "b.py": "", "c.ts": ""})
    languages = detect_languages(repo)
    assert languages == {"Python": 2, "TypeScript": 1}


def test_detect_archetype_inference_beats_training(tmp_path):
    # A repo with both inference and training entrypoints reads as inference.
    repo = _make_repo(tmp_path, {"train.py": "", "predict.py": ""})
    assert detect_archetype(repo, detect_languages(repo)) == "inference"


def test_detect_services_matches_catalog_and_keeps_evidence(tmp_path):
    repo = _make_repo(tmp_path, {"main.py": "import openai\nclient = openai.OpenAI()\n"})
    hits = detect_services(repo)
    keys = {h.key for h in hits}
    assert "openai" in keys
    # The matching line is kept as evidence for a later contribution PR.
    openai_hit = next(h for h in hits if h.key == "openai")
    assert "openai" in openai_hit.line and openai_hit.path == "main.py"


def test_audit_produces_valid_scaffold(tmp_path):
    repo = _make_repo(tmp_path, {"predict.py": "import anthropic\n"})
    result = audit_repo(repo, use_llm=False)
    # The scaffold is a valid model and is marked as a scaffold.
    assert result.model["maturity"] == "scaffold"
    assert validate_model(result.model).is_valid()
    assert result.archetype == "inference"


def test_audit_prefills_pricing_from_catalog(tmp_path):
    repo = _make_repo(tmp_path, {"app.py": "import openai\n"})
    result = audit_repo(repo, use_llm=False)
    apis = result.model["pricing"]["external_apis"]
    openai_block = next(b for b in apis if b["service_key"] == "openai")
    # The pricing block points at the provider page; price and usage stay TODO.
    assert openai_block["price_per_unit"]["source_url"].startswith("https://openai.com")
    assert openai_block["price_per_unit"]["status"] == "TODO"
    assert openai_block["usage_per_canonical_unit"]["status"] == "TODO"


def test_audit_rejects_non_directory(tmp_path):
    with pytest.raises(NotADirectoryError):
        audit_repo(tmp_path / "does-not-exist")


def test_audit_attaches_static_analysis_block(tmp_path):
    repo = _make_repo(
        tmp_path,
        {"train.py": "import torch\nmax_iters = 500000\n"},
    )
    result = audit_repo(repo, use_llm=False)
    analysis = result.model["analysis"]
    # The static read owns the block and the sourced work-size number.
    assert analysis["evidence_source"] == "static"
    assert analysis["workload_kind"] == "training"
    assert analysis["total_work_value"] == 500000
    assert analysis["total_work_source"] == "train.py::max_iters"
    # The block carries no `value` node, so it does not skew the honesty counts.
    assert "value" not in analysis
    assert validate_model(result.model).is_valid()


def test_audit_run_without_consent_records_note_and_completes(tmp_path, monkeypatch):
    # Point consent storage at an empty temp dir: no consent has been granted.
    monkeypatch.setenv("COST_RUNNING_REGISTRY_DIR", str(tmp_path / "cfg"))
    repo = _make_repo(
        tmp_path,
        {"train.py": "import torch\n", "tests/test_x.py": "def test_x():\n    assert True\n"},
    )
    result = audit_repo(repo, run=True, use_llm=False)
    # Nothing ran, but the audit still produced a valid model with an honest note.
    assert result.slice_result is None
    assert "consent" in result.model["analysis"]["run_note"].lower()
    assert "measurement" not in result.model
    assert validate_model(result.model).is_valid()


def test_audit_run_with_consent_folds_in_measured_power(tmp_path, monkeypatch):
    monkeypatch.setenv("COST_RUNNING_REGISTRY_DIR", str(tmp_path / "cfg"))
    from cost_running.application.execution import record_run_consent

    record_run_consent(True)
    repo = _make_repo(
        tmp_path,
        {
            "train.py": "import torch\n",
            "tests/test_x.py": "def test_x():\n    assert sum(range(1000)) > 0\n",
        },
    )
    result = audit_repo(repo, run=True, use_llm=False, timeout=60)
    assert result.slice_result is not None
    # A measurement block records what actually ran.
    measurement = result.model["measurement"]
    assert measurement["exit_code"] == 0
    assert measurement["slice_seconds"] > 0
    # The measured power replaced the archetype guess, still honestly `estimated`.
    power = result.model["assumptions"]["average_power_draw_watts"]
    assert power["status"] == "estimated"
    assert "Measured on this machine" in power["notes"]
    assert validate_model(result.model).is_valid()


def test_audit_capped_entrypoint_projects_completion(tmp_path, monkeypatch):
    """A capped entrypoint with a known fraction triggers the completion projection."""
    monkeypatch.setenv("COST_RUNNING_REGISTRY_DIR", str(tmp_path / "cfg"))
    from cost_running.application.execution import record_run_consent

    record_run_consent(True)
    # train.py accepts --max_iters; config.py gives the total work size.
    repo = _make_repo(
        tmp_path,
        {
            "train.py": (
                "import argparse, sys\n"
                "p = argparse.ArgumentParser()\n"
                "p.add_argument('--max_iters', type=int, default=10)\n"
                "args = p.parse_args()\n"
                "total = sum(range(args.max_iters))\n"
            ),
            "config.py": "max_iters = 600000\n",
        },
    )
    result = audit_repo(repo, run=True, use_llm=False, timeout=30)
    assert result.slice_result is not None
    assert result.slice_result.fraction_completed is not None
    # A completion projection was made and written into the model scenario.
    scenario = result.model["scenario"]
    runtime = scenario["runtime_seconds"]
    assert runtime["status"] == "estimated"
    notes = runtime.get("notes", "").lower()
    assert "slice" in notes or "fraction" in notes
    assert validate_model(result.model).is_valid()


def test_audit_target_gpu_without_local_gpu_records_not_applicable(tmp_path, monkeypatch):
    """When the local machine has no datacenter GPU, cloud_scenario is not-applicable."""
    monkeypatch.setenv("COST_RUNNING_REGISTRY_DIR", str(tmp_path / "cfg"))
    from cost_running.application.execution import record_run_consent
    from cost_running.infrastructure import hardware

    record_run_consent(True)
    monkeypatch.setattr(
        hardware,
        "detect_local_hardware",
        lambda: hardware.HardwareProfile(
            os="unknown", arch="unknown", cpu_model=None, logical_cores=None,
            memory_gb=None, gpu_model=None, cpu_power_key=None, gpu_power_key=None
        ),
    )
    repo = _make_repo(
        tmp_path,
        {
            "train.py": (
                "import argparse\n"
                "p = argparse.ArgumentParser()\n"
                "p.add_argument('--max_iters', type=int, default=5)\n"
                "args = p.parse_args()\n"
            ),
            "config.py": "max_iters = 100\n",
        },
    )
    result = audit_repo(repo, run=True, use_llm=False, timeout=30, target_gpu="H100")
    cs = result.model["cloud_scenario"]
    assert cs["applicable"] is False
    assert any("GPU" in r for r in cs["reasons"])
