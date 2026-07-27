"""Tests for the Green Algorithms estimator, profilers, and hardware mapping."""

from __future__ import annotations

import pytest

from cost_running.infrastructure.green_algorithms import (
    GPU_PEAK_BF16_TFLOPS,
    GPU_TDP_W,
    active_power_watts,
    estimate,
)
from cost_running.infrastructure.hardware import (
    _match_cpu_key,
    _match_gpu_key,
    detect_local_hardware,
)
from cost_running.infrastructure.profilers import rapl_average_power_watts


def test_active_power_matches_worked_example():
    # 1 A100 (400 W) plus 16 GB memory at 0.3725 W/GB.
    assert round(active_power_watts(gpu="A100", memory_gb=16), 2) == 405.96


def test_active_power_rejects_unknown_gpu():
    with pytest.raises(ValueError):
        active_power_watts(gpu="NOT-A-GPU")


def test_estimate_energy_and_carbon():
    r = estimate(runtime_seconds=0.1, active_power_w=405.96, pue=1.135, grid_gco2e_per_kwh=55)
    # Energy in kWh and carbon in gCO2e for a tenth of a second.
    assert round(r.carbon_gco2e, 6) == 0.000704
    assert r.energy_kwh > 0


def test_rapl_power_math_and_wrap_refusal():
    # 5 J over 0.5 s is 10 W.
    assert rapl_average_power_watts(1_000_000, 6_000_000, 0.5) == 10.0
    # A counter that went backwards (wrap/reset) must not be reported.
    assert rapl_average_power_watts(6_000_000, 1_000_000, 0.5) is None
    # A non-positive interval yields no rate.
    assert rapl_average_power_watts(0, 1_000_000, 0.0) is None


def test_hardware_key_matching():
    # Apple silicon is recognised by architecture.
    assert _match_cpu_key(None, "arm64") == "apple-m-series"
    assert _match_cpu_key("AMD EPYC 9654", "x86_64") == "epyc-9654"
    # A datacenter GPU name maps to a table key; a consumer GPU does not.
    assert _match_gpu_key("NVIDIA A100-SXM4-80GB") == "A100"
    assert _match_gpu_key("NVIDIA H100 80GB HBM3") == "H100"
    assert _match_gpu_key("NVIDIA GeForce RTX 4090") is None


def test_detect_local_hardware_never_raises():
    # Detection must degrade gracefully; it always returns a profile.
    profile = detect_local_hardware()
    assert profile.os in {"Darwin", "Linux", "Windows"}


def test_blackwell_and_cdna3_are_in_the_tables():
    # The catalogue carries the current mainstream accelerators.
    for key in ("B100", "B200", "MI325X"):
        assert key in GPU_TDP_W and key in GPU_PEAK_BF16_TFLOPS
