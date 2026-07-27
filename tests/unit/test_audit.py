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
    result = audit_repo(repo)
    # The scaffold is a valid model and is marked as a scaffold.
    assert result.model["maturity"] == "scaffold"
    assert validate_model(result.model).is_valid()
    assert result.archetype == "inference"


def test_audit_prefills_pricing_from_catalog(tmp_path):
    repo = _make_repo(tmp_path, {"app.py": "import openai\n"})
    result = audit_repo(repo)
    apis = result.model["pricing"]["external_apis"]
    openai_block = next(b for b in apis if b["service_key"] == "openai")
    # The pricing block points at the provider page; price and usage stay TODO.
    assert openai_block["price_per_unit"]["source_url"].startswith("https://openai.com")
    assert openai_block["price_per_unit"]["status"] == "TODO"
    assert openai_block["usage_per_canonical_unit"]["status"] == "TODO"


def test_audit_rejects_non_directory(tmp_path):
    with pytest.raises(NotADirectoryError):
        audit_repo(tmp_path / "does-not-exist")
