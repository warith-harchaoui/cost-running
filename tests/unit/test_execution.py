"""Tests for the consent gate and the profiling slice runner."""

from __future__ import annotations

import sys

import pytest

from cost_running.application import execution as ex


@pytest.fixture()
def isolated_config(tmp_path, monkeypatch):
    """Point consent storage at a temp dir so tests never touch real config."""
    monkeypatch.setenv("COST_RUNNING_REGISTRY_DIR", str(tmp_path))
    return tmp_path


def test_consent_absent_by_default(isolated_config):
    assert ex.has_run_consent() is False


def test_consent_refused_non_interactively(isolated_config):
    # A session that cannot read a reply (EOFError) must refuse, never assume.
    def raise_eof(_prompt):
        raise EOFError()

    assert ex.require_run_consent(prompt=raise_eof) is False
    assert ex.has_run_consent() is False


def test_consent_granted_and_remembered(isolated_config):
    assert ex.require_run_consent(prompt=lambda _p: "y") is True
    assert ex.has_run_consent() is True
    # A second call is honoured silently, without asking again.
    calls = []

    def record(_p):
        calls.append(1)
        return "n"

    assert ex.require_run_consent(prompt=record) is True
    assert calls == []  # not asked again


def test_consent_declined_is_recorded(isolated_config):
    assert ex.require_run_consent(prompt=lambda _p: "no") is False
    assert ex.has_run_consent() is False


def test_run_slice_refuses_without_consent(isolated_config, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(RuntimeError, match="consent"):
        ex.run_slice(repo, [sys.executable, "-c", "pass"])


def test_run_slice_measures_and_projects_completion(isolated_config, tmp_path):
    ex.record_run_consent(True)
    repo = tmp_path / "repo"
    repo.mkdir()
    # A cheap, deterministic command; no cProfile wrap for a bare -c form.
    cmd = [sys.executable, "-c", "s = sum(range(100000)); assert s > 0"]
    result = ex.run_slice(repo, cmd, timeout=30, fraction_completed=0.5, profile=False)

    m = result.measurement
    assert m.workload_exit_code == 0
    assert m.duration_seconds > 0
    # Energy and power are always labelled estimated, never measured.
    assert m.power_status == "estimated"
    # fraction 0.5 -> the whole run is twice the slice.
    assert result.completion is not None
    assert result.completion.applicable
    assert round(result.completion.runtime_seconds, 4) == round(m.duration_seconds * 2, 4)


def test_run_slice_rejects_non_directory(isolated_config, tmp_path):
    ex.record_run_consent(True)
    missing = tmp_path / "nope"
    with pytest.raises(NotADirectoryError):
        ex.run_slice(missing, [sys.executable, "-c", "pass"])


def test_cprofile_wrap_only_rewrites_module_form():
    out = "/tmp/x.prof"
    # A `-m module` form is wrapped so cProfile runs the module.
    wrapped = ex._wrap_with_cprofile([sys.executable, "-m", "pytest", "-q"], out)
    assert wrapped == [sys.executable, "-m", "cProfile", "-o", out, "-m", "pytest", "-q"]
    # A bare `-c` form is left alone rather than mis-wrapped.
    bare = [sys.executable, "-c", "pass"]
    assert ex._wrap_with_cprofile(bare, out) == bare
