"""Tests for the diff use case: dimensional drift detection and the CI gate."""

from __future__ import annotations

import copy

from cost_running.application.diff import diff_models


def _make_model(runtime: float, energy: float, cost: float) -> dict:
    return {
        "scenario": {
            "name": "default",
            "runtime_seconds": {"value": runtime, "status": "measured"},
            "local_compute": {
                "energy_kwh": {"value": energy, "status": "estimated"},
                "electricity_cost_usd": {"value": cost, "status": "estimated"},
            },
        }
    }


def test_diff_within_threshold_returns_no_drifts():
    old = _make_model(1.0, 1e-5, 3e-6)
    new = _make_model(1.05, 1e-5, 3e-6)  # 5% runtime drift, well under 10%
    result = diff_models(old, new)
    assert result.is_within_threshold(0.10)
    assert not result.above_threshold(0.10)


def test_diff_above_threshold_is_detected():
    old = _make_model(1.0, 1e-5, 3e-6)
    new = _make_model(1.5, 1e-5, 3e-6)  # 50% runtime drift
    result = diff_models(old, new)
    assert not result.is_within_threshold(0.10)
    drifts = result.above_threshold(0.10)
    assert any("runtime_seconds" in d.path for d in drifts)


def test_diff_relative_change_is_correct():
    old = _make_model(1.0, 1e-5, 3e-6)
    new = _make_model(1.2, 1e-5, 3e-6)
    result = diff_models(old, new)
    runtime_item = next(d for d in result.items if "runtime_seconds" in d.path)
    assert abs(runtime_item.relative_change - 0.20) < 1e-9


def test_diff_detects_added_and_removed_quantities():
    old = _make_model(1.0, 1e-5, 3e-6)
    new = copy.deepcopy(old)
    # Remove electricity_cost_usd from new.
    del new["scenario"]["local_compute"]["electricity_cost_usd"]
    # Add water to new.
    new["scenario"]["local_compute"]["water_liters"] = {"value": 0.001, "status": "estimated"}
    result = diff_models(old, new)
    assert any("electricity_cost_usd" in p for p in result.only_in_old)
    assert any("water_liters" in p for p in result.only_in_new)


def test_diff_zero_old_value_handled_gracefully():
    old = _make_model(0.0, 1e-5, 3e-6)
    new = _make_model(1.0, 1e-5, 3e-6)
    result = diff_models(old, new)
    runtime_item = next(d for d in result.items if "runtime_seconds" in d.path)
    # Zero old value → infinite relative change.
    assert runtime_item.relative_change == float("inf")


def test_diff_report_is_json_serialisable():
    import json

    old = _make_model(1.0, 1e-5, 3e-6)
    new = _make_model(2.0, 1e-5, 3e-6)
    result = diff_models(old, new)
    report = result.report(0.10)
    # Should serialise without error.
    json.dumps(report)
    assert not report["within_threshold"]
    assert report["drifts_above_threshold"]
