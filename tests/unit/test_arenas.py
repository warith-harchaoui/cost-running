"""Tests for the public-arena scraper (network mocked — no real HTTP calls)."""

from __future__ import annotations

from unittest.mock import patch

from cost_running.infrastructure import arenas


def _fake_hf_page(offset: int, length: int) -> dict:
    """Return a fake datasets-server response page."""
    total = 5
    rows = []
    for i in range(offset, min(offset + length, total)):
        rows.append({"row": {
            "fullname": f"org/model-{i}",
            "Average ⬆️": float(i * 10),
            "#Params (B)": float(i + 1),
            "Architecture": "LlamaForCausalLM",
            "IFEval": float(i),
            "BBH": float(i),
            "MATH Lvl 5": float(i),
            "GPQA": float(i),
            "MUSR": float(i),
            "MMLU-PRO": float(i),
            "CO₂ cost (kg)": float(i) * 0.1,
        }})
    return {"rows": rows}


def test_fetch_hf_leaderboard_paginates_and_normalises():
    call_count = [0]

    def fake_get(url, timeout=15.0):
        offset = int(url.split("offset=")[1].split("&")[0])
        length = int(url.split("length=")[1])
        call_count[0] += 1
        return _fake_hf_page(offset, length)

    with patch.object(arenas, "_get_json", side_effect=fake_get):
        rows = arenas.fetch_hf_leaderboard(limit=5)

    assert len(rows) == 5
    assert all("model" in r for r in rows)
    assert all("co2_kg" in r for r in rows)
    assert all("retrieved_date" in r for r in rows)
    assert all("source" in r for r in rows)
    # Column aliases applied: no raw Unicode key names in output.
    assert all("Average ⬆️" not in r for r in rows)


def test_fetch_hf_leaderboard_returns_empty_on_failure():
    with patch.object(arenas, "_get_json", return_value=None):
        rows = arenas.fetch_hf_leaderboard(limit=10)
    assert rows == []


def test_fetch_hf_leaderboard_stops_at_limit():
    def fake_get(url, timeout=15.0):
        offset = int(url.split("offset=")[1].split("&")[0])
        length = int(url.split("length=")[1])
        return _fake_hf_page(offset, length)

    with patch.object(arenas, "_get_json", side_effect=fake_get):
        rows = arenas.fetch_hf_leaderboard(limit=3)

    assert len(rows) == 3


def test_fetch_arena_returns_empty_stub():
    # The arena fetcher is a stub until a stable endpoint is confirmed.
    rows = arenas.fetch_arena()
    assert rows == []


def test_scrape_aggregates_sources():
    with patch.object(arenas, "_get_json", return_value={"rows": []}):
        result = arenas.scrape(sources=["hf_leaderboard", "arena"])
    assert set(result) == {"hf_leaderboard", "arena"}
    assert isinstance(result["hf_leaderboard"], list)
    assert isinstance(result["arena"], list)


def test_scrape_raises_on_unknown_source():
    import pytest
    with pytest.raises(ValueError, match="Unknown source"):
        arenas.scrape(sources=["does_not_exist"])


def test_available_sources_lists_registered_keys():
    sources = arenas.available_sources()
    assert "hf_leaderboard" in sources
    assert "arena" in sources
