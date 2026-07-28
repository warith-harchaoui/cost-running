"""Tests for the static structural reading (deterministic, no network)."""

from __future__ import annotations

import sys

from cost_running.infrastructure import code_analysis as ca


def _make_repo(tmp_path, files):
    """Write a mapping of relative paths to contents under tmp_path."""
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def test_static_reads_training_and_work_size(tmp_path):
    repo = _make_repo(
        tmp_path,
        {
            "train.py": "import torch\nmax_iters = 600000\nbatch_size = 12\n",
            "tests/test_smoke.py": "def test_ok():\n    assert True\n",
        },
    )
    a = ca.analyze(repo, use_llm=False)
    assert a.workload_kind == "training"
    assert "pytorch" in a.frameworks
    assert a.compute_bound is True
    assert a.total_work is not None
    assert a.total_work.unit == "max_iters"
    assert a.total_work.value == 600000
    # The number is sourced back to the file it was read from.
    assert a.total_work.source == "train.py::max_iters"
    assert a.evidence_source == "static"


def test_static_detects_tests_with_running_interpreter(tmp_path):
    repo = _make_repo(tmp_path, {"tests/test_a.py": "def test_a():\n    assert 1\n"})
    a = ca.analyze(repo, use_llm=False)
    assert a.has_tests is True
    # The command uses the running interpreter so it is identical across OSes.
    assert a.test_command == [sys.executable, "-m", "pytest", "-q", "-x"]


def test_static_quantities_never_invented_without_a_source(tmp_path):
    # No config key and no framework: the reading declines to guess.
    repo = _make_repo(tmp_path, {"main.py": "print('hello')\n"})
    a = ca.analyze(repo, use_llm=False)
    assert a.total_work is None
    assert a.compute_bound is None
    assert a.workload_kind == "unknown"
    assert a.has_tests is False
    assert a.test_command is None


def test_epochs_key_also_reads_as_training(tmp_path):
    repo = _make_repo(
        tmp_path,
        {"config.yaml": "num_train_epochs: 3\nlearning_rate: 0.001\n", "run.py": "import jax\n"},
    )
    a = ca.analyze(repo, use_llm=False)
    assert a.workload_kind == "training"
    assert a.total_work is not None and a.total_work.value == 3
    assert "jax" in a.frameworks


def test_capped_entrypoint_finds_train_py(tmp_path):
    repo = _make_repo(tmp_path, {"train.py": "pass\n"})
    work = ca.WorkSize(unit="max_iters", value=600000, source="train.py::max_iters")
    cmd, frac = ca.capped_entrypoint_command(repo, work, cap_fraction=0.001)
    assert cmd is not None
    assert cmd[-2] == "--max_iters"
    assert cmd[-1] == "600"
    assert abs(frac - 600 / 600000) < 1e-9


def test_capped_entrypoint_prefers_train_py_over_main(tmp_path):
    # train.py has priority over main.py in _ENTRYPOINT_NAMES.
    repo = _make_repo(tmp_path, {"train.py": "pass\n", "main.py": "pass\n"})
    work = ca.WorkSize(unit="max_iters", value=1000, source="config.py::max_iters")
    cmd, _ = ca.capped_entrypoint_command(repo, work)
    assert "train.py" in cmd[1]


def test_capped_entrypoint_caps_at_minimum_one(tmp_path):
    repo = _make_repo(tmp_path, {"train.py": "pass\n"})
    work = ca.WorkSize(unit="epochs", value=1, source="config.yaml::epochs")
    cmd, frac = ca.capped_entrypoint_command(repo, work, cap_fraction=0.001)
    assert cmd[-1] == "1"
    assert frac == 1.0


def test_capped_entrypoint_no_entrypoint_returns_none(tmp_path):
    # No train.py / main.py / run.py / benchmark.py in the repo.
    repo = _make_repo(tmp_path, {"src/model.py": "pass\n"})
    work = ca.WorkSize(unit="max_iters", value=100000, source="src/config.py::max_iters")
    cmd, frac = ca.capped_entrypoint_command(repo, work)
    assert cmd is None
    assert frac is None
