"""Tests for the CLI adapter: exit codes, stdout routing, the init/validate/render flow."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from cost_running.cli.app import main


def _run(*argv: str) -> subprocess.CompletedProcess:
    """Run the CLI in a subprocess and capture stdout/stderr separately."""
    # Exercising the real process is the only way to prove stdout routing, which
    # a redirect (`render ... > out.md`) depends on.
    return subprocess.run(
        [sys.executable, "-m", "cost_running.cli.app", *argv],
        capture_output=True,
        text=True,
    )


def test_version_prints_to_stdout_and_exits_zero():
    proc = _run("--version")
    assert proc.returncode == 0
    assert "cost-running" in proc.stdout


def test_init_then_validate_flow(tmp_path):
    model_path = tmp_path / "cost_of_running.yaml"
    # init writes a starter model.
    assert main(["init", "--output", str(model_path)]) == 0
    assert model_path.exists()
    # The freshly scaffolded min template validates (its TODOs are honest, not errors).
    assert main(["validate", str(model_path)]) == 0


def test_init_refuses_to_overwrite_without_force(tmp_path):
    model_path = tmp_path / "m.yaml"
    model_path.write_text("schema_version: '1.0'\n")
    # Overwriting a hand-maintained model without --force is a usage error.
    assert main(["init", "--output", str(model_path)]) == 2


def test_validate_reports_errors_with_exit_one(tmp_path):
    broken = tmp_path / "broken.yaml"
    # Missing every required field: validation must fail with the operational code.
    broken.write_text("schema_version: '1.0'\nscenario: {}\n")
    assert main(["validate", str(broken)]) == 1


def test_render_writes_to_stdout(tmp_path):
    # Build a model file, then render it and confirm the report lands on stdout.
    model_path = tmp_path / "m.yaml"
    main(["init", "--template", "full", "--output", str(model_path)])
    proc = _run("render", str(model_path))
    assert proc.returncode == 0
    assert proc.stdout.startswith("# Cost of running")


def test_render_writes_to_output_file(tmp_path):
    model_path = tmp_path / "m.yaml"
    main(["init", "--template", "full", "--output", str(model_path)])
    report = tmp_path / "report.md"
    assert main(["render", str(model_path), "--output", str(report)]) == 0
    assert Path(report).read_text().startswith("# Cost of running")
