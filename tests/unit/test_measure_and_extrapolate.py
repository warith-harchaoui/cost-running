"""Tests for the measure use case and the grounded extrapolation model."""

from __future__ import annotations

import sys

from cost_running.application.extrapolate import (
    extrapolate_gpu,
    extrapolate_to_completion,
)
from cost_running.application.measure import measure_command


def test_measure_runs_command_and_reports_honest_power():
    # A trivial, fast command. Duration is always reported; energy is always
    # estimated (even when derived from a RAPL reading, since attribution to the
    # workload is an estimation step).
    result = measure_command([sys.executable, "-c", "sum(range(100000))"])
    assert result.duration_seconds >= 0
    # Power status is always estimated: RAPL gives system-package attribution,
    # not process-attributed energy.
    assert result.power_status == "estimated"
    # power_source distinguishes the tightness of the estimate.
    assert result.power_source in {"rapl_system_package", "tdp_estimate"}
    # The result records the machine it ran on, for later extrapolation.
    assert result.hardware.os in {"Darwin", "Linux", "Windows"}
    # Exit code is captured so a caller can detect a failed workload.
    assert result.workload_exit_code == 0
    # The JSON view exposes the derived status so a consumer need not re-derive it.
    assert result.to_dict()["power_status"] == result.power_status


def test_measure_captures_nonzero_exit_code():
    result = measure_command([sys.executable, "-c", "raise SystemExit(42)"])
    assert result.workload_exit_code == 42
    assert any("exited with code 42" in w for w in result.warnings)


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


def test_completion_scales_slice_to_whole():
    # A slice covering 1% of the work projects to 100x its cost.
    r = extrapolate_to_completion(
        fraction_completed=0.01,
        slice_runtime_seconds=30.0,
        slice_energy_kwh=0.02,
        slice_carbon_gco2e=1.5,
        slice_cost_usd=0.004,
    )
    assert r.applicable
    assert r.scale == 100.0
    assert round(r.runtime_seconds) == 3000
    assert round(r.energy_kwh, 3) == 2.0
    assert round(r.carbon_gco2e, 1) == 150.0
    assert round(r.cost_usd, 3) == 0.4
    # It is an estimate that states its model, assumptions, and limits.
    assert r.status == "estimated"
    assert r.method and r.assumptions and r.limits


def test_completion_leaves_missing_dimensions_none():
    # Only runtime was measured; energy/carbon/cost stay None rather than invented.
    r = extrapolate_to_completion(fraction_completed=0.5, slice_runtime_seconds=10.0)
    assert r.applicable
    assert r.runtime_seconds == 20.0
    assert r.energy_kwh is None
    assert r.carbon_gco2e is None
    assert r.cost_usd is None


def test_completion_refuses_bad_fraction():
    # A fraction outside (0, 1] cannot be inverted into an honest multiplier.
    for bad in (0.0, -0.1, 1.5):
        r = extrapolate_to_completion(fraction_completed=bad, slice_runtime_seconds=10.0)
        assert not r.applicable
        assert r.runtime_seconds is None
        assert r.reasons


def test_completion_refuses_nonpositive_slice_runtime():
    r = extrapolate_to_completion(fraction_completed=0.5, slice_runtime_seconds=0.0)
    assert not r.applicable
    assert r.reasons
