"""Tests for the measure use case and the grounded extrapolation model."""

from __future__ import annotations

import sys

from cost_running.application.extrapolate import extrapolate_gpu
from cost_running.application.measure import measure_command


def test_measure_runs_command_and_reports_honest_power():
    # A trivial, fast command. Duration is always measured; power is measured only
    # when RAPL was readable, which we do not assume in CI.
    result = measure_command([sys.executable, "-c", "sum(range(100000))"])
    assert result.duration_seconds >= 0
    assert result.power_status in {"measured", "estimated"}
    # The result records the machine it ran on, for later extrapolation.
    assert result.hardware.os in {"Darwin", "Linux", "Windows"}
    # The JSON view exposes the derived status so a consumer need not re-derive it.
    assert result.to_dict()["power_status"] == result.power_status


def test_measure_rejects_empty_command():
    import pytest

    with pytest.raises(ValueError):
        measure_command([])


def test_extrapolate_gpu_rebases_runtime_by_throughput():
    # H100 has ~3.17x the BF16 throughput of A100, so the same compute-bound work
    # should take about a third of the time.
    r = extrapolate_gpu(source_gpu="A100", target_gpu="H100", source_runtime_seconds=1.0)
    assert r.applicable
    # 312 / 989 TFLOP/s peak ratio.
    assert round(r.runtime_seconds, 3) == 0.315
    # It is an estimate with a stated model, assumptions, and limits.
    assert r.status == "estimated"
    assert r.method and r.assumptions and r.limits


def test_extrapolate_carbon_only_when_grid_given():
    without = extrapolate_gpu(source_gpu="A100", target_gpu="H100", source_runtime_seconds=1.0)
    assert without.carbon_gco2e is None
    withgrid = extrapolate_gpu(
        source_gpu="A100", target_gpu="H100", source_runtime_seconds=1.0, grid_gco2e_per_kwh=55
    )
    assert withgrid.carbon_gco2e is not None and withgrid.carbon_gco2e > 0


def test_extrapolate_refuses_unknown_target():
    # A device missing from the tables is outside the model's regime; refuse.
    r = extrapolate_gpu(source_gpu="A100", target_gpu="NOT-A-GPU", source_runtime_seconds=1.0)
    assert not r.applicable
    assert r.confidence == "not-applicable"
    assert r.runtime_seconds is None
    assert r.reasons
