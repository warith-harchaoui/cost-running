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


def test_stale_threshold_is_category_aware():
    # Volatile data (prices, grid) is stale after a month; hardware specs after a year.
    assert registry.stale_threshold_days("services") == 30
    assert registry.stale_threshold_days("country") == 30
    assert registry.stale_threshold_days("gpus") == 365
    assert registry.stale_threshold_days("cpus") == 365
    # An unknown kind, or none at all, falls back to the monthly default.
    assert registry.stale_threshold_days(None) == registry.STALE_DAYS
    assert registry.stale_threshold_days("mystery") == registry.STALE_DAYS


def test_kind_changes_staleness_verdict():
    from datetime import date, timedelta

    # A row sourced ~60 days ago: stale as a monthly-refreshed price, fresh as a
    # yearly-refreshed hardware spec. The same date, a different verdict by kind.
    sixty_days_ago = (date.today() - timedelta(days=60)).isoformat()
    row = {"retrieved_date": sixty_days_ago}
    assert registry.is_stale(row, "services")
    assert not registry.is_stale(row, "gpus")


def test_stale_rows_scan_groups_by_category():
    stale = registry.stale_rows()
    # The scan reports all three categories, each mapped to a list (possibly empty).
    assert set(stale) == {"gpus", "cpus", "services"}
    for keys in stale.values():
        assert isinstance(keys, list)
    # Bundled services carry no fetched price, so every one needs sourcing.
    assert stale["services"], "expected unsourced bundled services to be flagged"
    # Bundled hardware was sourced recently, so nothing hardware-side is stale yet.
    assert stale["gpus"] == []


def test_save_and_load_leaderboard(tmp_path):
    rows = [
        {"model": "org/model-A", "average_score": 42.5, "co2_kg": 1.2,
         "source": "https://hf.co/datasets/...", "retrieved_date": "2026-07-29"},
        {"model": "org/model-B", "average_score": 67.1, "co2_kg": 3.4,
         "source": "https://hf.co/datasets/...", "retrieved_date": "2026-07-29"},
    ]
    target = registry.save_leaderboard(rows, "hf_leaderboard", overlay=tmp_path)
    assert target.exists()
    loaded = registry.load_leaderboard(overlay=tmp_path)
    assert len(loaded) == 2
    models = {r["model"] for r in loaded}
    assert models == {"org/model-A", "org/model-B"}


def test_save_leaderboard_replaces_same_source(tmp_path):
    # A second save from the same source replaces previous rows, no duplicates.
    first = [{"model": "m1", "source": "https://s", "retrieved_date": "2026-07-28"}]
    second = [{"model": "m2", "source": "https://s", "retrieved_date": "2026-07-29"}]
    registry.save_leaderboard(first, "hf_leaderboard", overlay=tmp_path)
    registry.save_leaderboard(second, "hf_leaderboard", overlay=tmp_path)
    loaded = registry.load_leaderboard(overlay=tmp_path)
    models = {r["model"] for r in loaded}
    assert models == {"m2"}  # first batch gone, second batch present


def test_save_leaderboard_preserves_other_sources(tmp_path):
    # Saving from source A must not delete rows from source B.
    registry.save_leaderboard(
        [{"model": "a1", "source": "https://a", "retrieved_date": "2026-07-29"}],
        "hf_leaderboard", overlay=tmp_path,
    )
    registry.save_leaderboard(
        [{"model": "b1", "source": "https://b", "retrieved_date": "2026-07-29"}],
        "arena", overlay=tmp_path,
    )
    loaded = registry.load_leaderboard(overlay=tmp_path)
    models = {r["model"] for r in loaded}
    assert models == {"a1", "b1"}


def test_leaderboard_threshold_is_weekly():
    assert registry.stale_threshold_days("leaderboard") == 7
