"""Tests for the HTML renderer and its SVG figures."""

from __future__ import annotations

from cost_running.application.render_html import (
    figure_cost_breakdown,
    figure_honesty_legend,
    figure_scenario_comparison,
    render_html,
)


def test_render_html_is_valid_document(valid_model):
    html = render_html(valid_model)
    assert html.startswith("<!DOCTYPE html>")
    assert "<html lang=" in html
    assert "</html>" in html
    assert "Cost of running" in html


def test_render_html_contains_status_badges(valid_model):
    html = render_html(valid_model)
    # Status badges should appear for the quantities in the model.
    assert "badge-measured" in html or "badge-estimated" in html


def test_render_html_has_accessible_figures(valid_model):
    html = render_html(valid_model)
    # Every <figure> should have role="img" and an aria-label.
    assert 'role="img"' in html
    assert "aria-label=" in html
    assert "<figcaption>" in html


def test_render_html_never_mutates_the_model(valid_model):
    before = str(valid_model)
    render_html(valid_model)
    assert str(valid_model) == before


def test_figure_cost_breakdown_returns_svg(valid_model):
    scenario = valid_model["scenario"]
    svg = figure_cost_breakdown(scenario)
    assert svg.startswith("<svg")
    assert "</svg>" in svg
    assert 'role="img"' in svg


def test_figure_honesty_legend_returns_svg(valid_model):
    svg = figure_honesty_legend(valid_model)
    assert svg.startswith("<svg")
    assert "</svg>" in svg
    assert "estimated" in svg or "measured" in svg


def test_figure_scenario_comparison_requires_multiple_scenarios():
    single = [{"name": "only", "runtime_seconds": {"value": 1.0, "status": "measured"}}]
    assert figure_scenario_comparison(single) == ""


def test_figure_scenario_comparison_returns_svg_for_two_scenarios():
    two = [
        {"name": "fast", "totals": {"total_cost_usd": {"value": 0.001, "status": "estimated"}}},
        {"name": "slow", "totals": {"total_cost_usd": {"value": 0.003, "status": "estimated"}}},
    ]
    svg = figure_scenario_comparison(two)
    assert svg.startswith("<svg")
    assert "fast" in svg and "slow" in svg


def test_render_html_contains_dark_mode_css(valid_model):
    html = render_html(valid_model)
    assert "prefers-color-scheme: dark" in html
