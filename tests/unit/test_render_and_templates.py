"""Tests for the renderer and the bundled templates."""

from __future__ import annotations

import yaml

from cost_running import get_template_text
from cost_running.application import render_markdown, validate_model
from cost_running.domain import SCHEMA_VERSION


def test_render_contains_sections_and_shows_status(valid_model):
    md = render_markdown(valid_model)
    # The report is self-describing and surfaces the honesty status of numbers.
    assert "# Cost of running" in md
    assert "## Scenarios" in md
    assert "(measured)" in md  # the measured runtime
    assert "(estimated)" in md  # a derived value


def test_render_never_mutates_the_model(valid_model):
    before = str(valid_model)
    render_markdown(valid_model)
    assert str(valid_model) == before


def test_min_template_carries_schema_version_and_parses():
    data = yaml.safe_load(get_template_text("min"))
    assert data["schema_version"] == SCHEMA_VERSION
    # The min template is a TODO scaffold; it must load (validate) without errors.
    assert validate_model(data).is_valid()


def test_full_template_is_valid_and_honest():
    data = yaml.safe_load(get_template_text("full"))
    result = validate_model(data)
    assert result.is_valid()
    # The worked example is internally consistent, so no weakest-link warning.
    assert not any("outrank" in i.message for i in result.warnings)
