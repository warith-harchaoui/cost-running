"""Tests for the deterministic catalog-contribution core (the auto-PR groundwork)."""

from __future__ import annotations

import pytest

from cost_running.application.contribute import (
    EvidenceSnippet,
    branch_name,
    build_contribution,
    catalog_file_for,
    insert_row_into_catalog,
    render_pr_body,
)


def _row() -> dict:
    return {
        "key": "H20",
        "tdp_w": 400,
        "peak_bf16_tflops": 148,
        "source_url": "https://example.com/h20",
        "retrieved_date": "2026-07-27",
    }


def test_build_contribution_requires_provenance():
    with pytest.raises(ValueError):
        build_contribution("gpus", {"key": "H20", "tdp_w": 400}, rationale="found in code")


def test_catalog_file_and_branch_name():
    assert catalog_file_for("gpus") == "hardware.yaml"
    assert catalog_file_for("services") == "services.yaml"
    contribution = build_contribution("gpus", _row(), rationale="found in code")
    assert branch_name(contribution) == "catalog/add-gpus-h20"


def test_insert_row_preserves_comments_and_adds_under_section():
    text = "# a header comment\ngpus:\n  - key: A100\n    tdp_w: 400\ncpus:\n  - key: x\n"
    out = insert_row_into_catalog(text, "gpus", _row())
    # The comment and the existing rows survive; the new key lands under gpus.
    assert "# a header comment" in out
    assert "key: A100" in out
    assert "key: H20" in out
    # The new GPU is inserted before the cpus section, not appended after it.
    assert out.index("key: H20") < out.index("cpus:")


def test_render_pr_body_carries_row_and_evidence():
    contribution = build_contribution(
        "gpus",
        _row(),
        rationale="Detected an H20 in the deployment notes.",
        evidence=[EvidenceSnippet(path="deploy.py", text="GPU = 'H20'", note="hardware pin")],
    )
    body = render_pr_body(contribution)
    assert "H20" in body
    assert "https://example.com/h20" in body  # provenance
    assert "deploy.py" in body and "GPU = 'H20'" in body  # evidence
