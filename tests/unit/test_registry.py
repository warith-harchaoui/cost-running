"""Tests for the hardware/service catalog: loading, overlay, provenance, freshness."""

from __future__ import annotations

import pytest

from cost_running.infrastructure import registry


def test_bundled_catalog_loads_with_provenance():
    catalog = registry.hardware_catalog()
    # Every bundled GPU row carries a source URL: the catalog is sourced, not folklore.
    for key, row in catalog["gpus"].items():
        assert row.get("source_url"), f"{key} has no source_url"
    # The service catalog knows where to price each service.
    services = registry.service_catalog()
    assert services["openai"]["pricing_source_url"].startswith("https://")


def test_add_hardware_requires_provenance(tmp_path):
    # An entry without a source is refused; the catalog only stores sourced values.
    with pytest.raises(ValueError):
        registry.add_hardware(
            "gpus",
            {"key": "H20", "tdp_w": 400},
            overlay=tmp_path,
        )


def test_add_hardware_writes_overlay_and_merges(tmp_path):
    registry.add_hardware(
        "gpus",
        {
            "key": "H20",
            "tdp_w": 400,
            "peak_bf16_tflops": 148,
            "source_url": "https://example.com/h20",
            "retrieved_date": "2026-07-27",
        },
        overlay=tmp_path,
    )
    # The overlay row appears in the merged catalog and the derived power table.
    catalog = registry.hardware_catalog(overlay=tmp_path)
    assert "H20" in catalog["gpus"]
    assert registry.gpu_tdp_w(overlay=tmp_path)["H20"] == 400


def test_overlay_overrides_bundled_key(tmp_path):
    # A local correction to an existing key wins over the bundled value.
    registry.add_hardware(
        "gpus",
        {
            "key": "A100",
            "tdp_w": 410,
            "source_url": "https://example.com/a100-corrected",
            "retrieved_date": "2026-07-27",
        },
        overlay=tmp_path,
    )
    assert registry.gpu_tdp_w(overlay=tmp_path)["A100"] == 410


def test_add_service_accepts_pricing_url_as_provenance(tmp_path):
    target = registry.add_service(
        {
            "key": "acme-llm",
            "name": "Acme LLM",
            "pricing_source_url": "https://acme.example/pricing",
            "retrieved_date": "2026-07-27",
        },
        overlay=tmp_path,
    )
    assert target.exists()
    assert "acme-llm" in registry.service_catalog(overlay=tmp_path)


def test_stale_detection():
    # A row retrieved long ago is stale; a missing date is treated as stale too.
    assert registry.is_stale({"retrieved_date": "2000-01-01"})
    assert registry.is_stale({})
    from datetime import date

    assert not registry.is_stale({"retrieved_date": date.today().isoformat()})
