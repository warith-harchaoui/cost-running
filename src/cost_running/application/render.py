"""
Render a cost model to a human-readable Markdown report.

Module summary
--------------
The YAML model is the machine-readable source of truth; this use case produces
its human-readable companion. The report keeps every status label visible, so a
reader sees at a glance which numbers are measured, which are estimated, and
which still await a human. Rendering never mutates the model and never validates
it; a caller that wants both runs validation separately.

Author
------
Project maintainers.
"""

from __future__ import annotations

from typing import Any

from .validate import normalize_scenarios


def _escape(value: Any) -> str:
    """Return a Markdown-table-safe string for a value.

    Parameters
    ----------
    value : Any
        The value to stringify.

    Returns
    -------
    str
        The value as text with pipe characters escaped so they cannot break a
        table column, and ``None`` shown as an em-free dash placeholder.
    """
    if value is None:
        return "-"
    # A literal pipe would start a new table cell; escape it.
    return str(value).replace("|", "\\|")


def _quantity_cell(obj: Any) -> str:
    """Format a quantity mapping as ``value unit (status)`` for a table cell.

    Parameters
    ----------
    obj : Any
        A quantity-shaped mapping, or a bare value.

    Returns
    -------
    str
        A compact, human-readable rendering that always surfaces the status.
    """
    # A bare (non-mapping) value has no status to show.
    if not isinstance(obj, dict):
        return _escape(obj)
    value = obj.get("value")
    unit = obj.get("unit")
    status = obj.get("status")
    # Compose value, optional unit, then the status in parentheses.
    text = _escape(value)
    if unit:
        text = f"{text} {_escape(unit)}"
    if status:
        text = f"{text} ({_escape(status)})"
    return text


def _render_header(data: dict[str, Any]) -> list[str]:
    """Return the report title and date lines."""
    lines = ["# Cost of running", ""]
    # Surface when the model was last reviewed so a reader can judge freshness.
    if data.get("date_updated"):
        lines.append(f"_Last updated: {_escape(data['date_updated'])}._")
        lines.append("")
    # State the schema line so the report is self-describing.
    if data.get("schema_version"):
        lines.append(f"_Schema version: {_escape(data['schema_version'])}._")
        lines.append("")
    return lines


def _render_unit(data: dict[str, Any]) -> list[str]:
    """Return the canonical-unit-of-work section."""
    cuow = data.get("canonical_unit_of_work")
    if not isinstance(cuow, dict):
        return []
    lines = ["## Canonical unit of work", ""]
    # Name and its honesty status anchor the whole model.
    lines.append(f"- **Unit**: {_escape(cuow.get('name'))} ({_escape(cuow.get('status'))})")
    if cuow.get("description"):
        lines.append(f"- **Description**: {_escape(cuow['description'])}")
    # List out-of-scope items so the boundary of the measurement is explicit.
    out_of_scope = cuow.get("out_of_scope")
    if isinstance(out_of_scope, list) and out_of_scope:
        lines.append("- **Out of scope**:")
        lines.extend(f"  - {_escape(item)}" for item in out_of_scope)
    lines.append("")
    return lines


def _render_deployment(data: dict[str, Any]) -> list[str]:
    """Return the deployment section as a key-value list."""
    deployment = data.get("deployment")
    if not isinstance(deployment, dict) or not deployment:
        return []
    lines = ["## Deployment", ""]
    # Render each declared attribute; the set varies by model, so iterate.
    for key, value in deployment.items():
        lines.append(f"- **{_escape(key)}**: {_escape(value)}")
    lines.append("")
    return lines


def _render_scenario(scenario: dict[str, Any]) -> list[str]:
    """Return one scenario's metric table."""
    name = scenario.get("name", "scenario")
    lines = [f"### Scenario: {_escape(name)}", ""]
    if scenario.get("description"):
        lines.append(_escape(scenario["description"]))
        lines.append("")
    # A two-column table: the metric and its value-with-status.
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Runtime | {_quantity_cell(scenario.get('runtime_seconds'))} |")

    # Local compute dimensions, in the canonical reading order.
    compute = scenario.get("local_compute", {})
    if isinstance(compute, dict):
        for key, label in (
            ("energy_kwh", "Energy"),
            ("electricity_cost_usd", "Electricity cost"),
            ("carbon_gco2e", "Carbon"),
            ("water_liters", "Water"),
        ):
            # Only show a dimension the scenario actually reports.
            if key in compute:
                lines.append(f"| {label} | {_quantity_cell(compute[key])} |")

    # External API money, then the totals.
    if "external_api_cost_usd" in scenario:
        lines.append(f"| External API cost | {_quantity_cell(scenario['external_api_cost_usd'])} |")
    totals = scenario.get("totals", {})
    if isinstance(totals, dict):
        if "total_cost_usd" in totals:
            lines.append(f"| **Total cost** | {_quantity_cell(totals['total_cost_usd'])} |")
        if "total_carbon_gco2e" in totals:
            lines.append(f"| **Total carbon** | {_quantity_cell(totals['total_carbon_gco2e'])} |")
    lines.append("")
    return lines


def _render_scenarios(data: dict[str, Any]) -> list[str]:
    """Return the scenarios section (one table per scenario)."""
    scenarios = normalize_scenarios(data)
    if not scenarios:
        return []
    lines = ["## Scenarios", ""]
    for scenario in scenarios:
        lines.extend(_render_scenario(scenario))
    return lines


def _render_exclusions(data: dict[str, Any]) -> list[str]:
    """Return the exclusions section, stating what is not counted."""
    exclusions = data.get("exclusions")
    if not isinstance(exclusions, list) or not exclusions:
        return []
    lines = ["## Exclusions", ""]
    lines.extend(f"- {_escape(item)}" for item in exclusions)
    lines.append("")
    return lines


def render_markdown(data: dict[str, Any]) -> str:
    """Render a cost model to a Markdown report string.

    Parameters
    ----------
    data : dict
        The parsed cost model.

    Returns
    -------
    str
        A Markdown document with the unit of work, deployment, scenario tables,
        and exclusions, every number carrying its honesty status.

    Examples
    --------
    >>> md = render_markdown({
    ...     "date_updated": "2026-07-27",
    ...     "canonical_unit_of_work": {"name": "one run", "status": "estimated"},
    ...     "deployment": {"provider": "local"},
    ...     "scenario": {"name": "default",
    ...                  "runtime_seconds": {"value": 1.0, "status": "measured"}},
    ... })
    >>> "# Cost of running" in md
    True
    """
    # Assemble the sections in reading order; each returns its own lines so the
    # composition stays flat and easy to reorder.
    lines: list[str] = []
    lines.extend(_render_header(data))
    lines.extend(_render_unit(data))
    lines.extend(_render_deployment(data))
    lines.extend(_render_scenarios(data))
    lines.extend(_render_exclusions(data))
    # A trailing note reminds the reader the YAML is the source of truth.
    lines.append("---")
    lines.append("")
    lines.append("_Generated from the YAML cost model. Edit the YAML, then re-render._")
    lines.append("")
    return "\n".join(lines)
