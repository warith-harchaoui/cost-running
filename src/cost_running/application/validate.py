"""
Validate a cost model against the schema and the honesty rules.

Module summary
--------------
Validation is where the honesty taxonomy stops being a convention and becomes
enforceable. This use case checks five families of rule: the schema version is
one this build understands, the required structure is present, every status is a
real label, numeric quantities are non-negative and carry a status, provenance
freshness is acceptable, and no derived value overclaims the honesty of its
inputs (the weakest-link rule). It returns a :class:`ValidationResult`; it never
prints or exits, so every delivery surface reports the verdict its own way.

Weakest-link overclaiming is an error, not a warning. The central proposition of
this tool is that every number states how much it can be trusted; a value that
falsely claims ``measured`` when its inputs are only ``estimated`` undermines that
proposition. An explicit model annotation (``# weakest-link-override``) is the
planned escape hatch for the rare case where the rule is genuinely not applicable.

Author
------
Project maintainers.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from ..domain.schema import SCHEMA_VERSION, schema_major
from ..domain.taxonomy import ESTIMATED, is_valid_status, status_strength, weakest
from .results import ValidationResult

_FRESHNESS_WARN_DAYS: int = 90


def _status_of(obj: Any) -> str | None:
    """Return the ``status`` of a quantity-shaped mapping, or ``None``."""
    if isinstance(obj, dict) and "status" in obj:
        return str(obj["status"])
    return None


def _is_date_string(value: str) -> bool:
    """Return whether ``value`` is a valid ``YYYY-MM-DD`` calendar date."""
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _days_since(value: str) -> int | None:
    """Return the number of days between ``value`` and today, or ``None``."""
    try:
        then = date.fromisoformat(value)
    except ValueError:
        return None
    return (date.today() - then).days


def normalize_scenarios(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the model's scenarios as a list, whichever spelling it used.

    A model carries either a single ``scenario`` mapping or a ``scenarios`` list.
    This flattens both into one list so callers iterate uniformly.

    Parameters
    ----------
    data : dict
        The cost model.

    Returns
    -------
    list of dict
        Zero or more scenario mappings.
    """
    if isinstance(data.get("scenarios"), list):
        return [s for s in data["scenarios"] if isinstance(s, dict)]
    if isinstance(data.get("scenario"), dict):
        return [data["scenario"]]
    return []


def _check_schema_version(data: dict[str, Any], result: ValidationResult) -> None:
    """Apply the schema-version compatibility policy."""
    if "schema_version" not in data:
        result.add(
            "warning",
            f'schema_version is missing; assuming "{SCHEMA_VERSION}". '
            f'Add `schema_version: "{SCHEMA_VERSION}"` at the top of the model.',
        )
        return

    declared = schema_major(data.get("schema_version"))
    current = schema_major(SCHEMA_VERSION)
    if declared is None:
        result.add(
            "error",
            f'schema_version must be a MAJOR.MINOR string, e.g. "{SCHEMA_VERSION}".',
        )
    elif declared > current:
        result.add(
            "error",
            f"schema_version {data['schema_version']!r} is newer than this build "
            "supports (1.x); upgrade cost-running.",
        )
    elif declared < current:
        result.add(
            "warning",
            f"schema_version {data['schema_version']!r} predates the locked 1.0 "
            f'schema; re-audit or bump it to "{SCHEMA_VERSION}".',
        )


def _check_required_fields(data: dict[str, Any], result: ValidationResult) -> None:
    """Verify the mandatory top-level structure is present and well-formed."""
    for field_name in ("date_updated", "canonical_unit_of_work", "deployment"):
        if field_name not in data:
            result.add("error", f"Missing required top-level field: {field_name}")

    if "date_updated" in data:
        updated = str(data["date_updated"])
        if updated == "YYYY-MM-DD":
            result.add("warning", "date_updated is still a template placeholder.")
        elif not _is_date_string(updated):
            result.add("error", "date_updated must be a YYYY-MM-DD calendar date.")

    cuow = data.get("canonical_unit_of_work")
    if isinstance(cuow, dict):
        if not cuow.get("name"):
            result.add("error", "canonical_unit_of_work.name is required.")
        status = _status_of(cuow)
        if status is not None and not is_valid_status(status):
            result.add("error", f"canonical_unit_of_work has invalid status {status!r}.")
    elif "canonical_unit_of_work" in data:
        result.add("error", "canonical_unit_of_work must be a mapping.")

    if not normalize_scenarios(data):
        result.add("error", "A model must define a `scenario` or a `scenarios` list.")


def _check_statuses_and_freshness(data: dict[str, Any], result: ValidationResult) -> None:
    """Check every quantity's status label and provenance freshness.

    Walks the whole model recursively so a status typo anywhere is caught, and
    warns when a sourced value's ``retrieved_date`` is stale.
    """

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            status = node.get("status")
            if status is not None and not is_valid_status(str(status)):
                result.add("error", f"{path or 'value'} has invalid status {str(status)!r}.")
            retrieved = node.get("retrieved_date")
            is_real_date = (
                isinstance(retrieved, str)
                and retrieved != "YYYY-MM-DD"
                and _is_date_string(retrieved)
            )
            if is_real_date:
                age = _days_since(retrieved)
                if age is not None and age > _FRESHNESS_WARN_DAYS:
                    result.add(
                        "warning",
                        f"{path or 'value'} provenance is {age} days old "
                        f"(> {_FRESHNESS_WARN_DAYS}); verify the source is current.",
                    )
            for key, child in node.items():
                walk(child, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{path}[{index}]")

    walk(data, "")


def _check_numeric_quantities(data: dict[str, Any], result: ValidationResult) -> None:
    """Check that every quantity with a numeric value is non-negative and carries a status.

    A mapping is treated as a quantity when it has a numeric ``value`` key. Such
    quantities must carry a ``status`` (so the consumer knows how to trust the
    number) and must not be negative (energy, time, and cost cannot be negative).

    Parameters
    ----------
    data : dict
        The cost model.
    result : ValidationResult
        Accumulator to record issues into.
    """

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            value = node.get("value")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if "status" not in node:
                    result.add(
                        "error",
                        f"{path or 'quantity'}.value is {value!r} but has no status; "
                        "every numeric quantity must declare its honesty label.",
                    )
                if value < 0:
                    result.add(
                        "error",
                        f"{path or 'quantity'}.value is {value!r}; "
                        "physical quantities (energy, time, cost) cannot be negative.",
                    )
            for key, child in node.items():
                walk(child, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{path}[{index}]")

    walk(data, "")


def _check_provenance(data: dict[str, Any], result: ValidationResult) -> None:
    """Warn when an estimated value in the assumptions block has no source URL.

    Estimated values in ``assumptions`` represent external facts (electricity
    price, grid intensity, hardware power) that must be sourced. A missing
    ``source_url`` is a warning rather than an error because the model can still
    be used, but the reader cannot verify or refresh the number.

    Parameters
    ----------
    data : dict
        The cost model.
    result : ValidationResult
        Accumulator to record issues into.
    """
    assumptions = data.get("assumptions")
    if not isinstance(assumptions, dict):
        return
    for key, value in assumptions.items():
        if not isinstance(value, dict):
            continue
        if value.get("status") == ESTIMATED and not value.get("source_url"):
            result.add(
                "warning",
                f"assumptions.{key} is estimated but has no source_url; "
                "add a sourced URL so the number can be verified and refreshed.",
            )


def _check_weakest_link(data: dict[str, Any], result: ValidationResult) -> None:
    """Error when a derived value overclaims the honesty of its inputs.

    Overclaiming is an *error* (not a warning) because it directly contradicts
    the weakest-link rule, which is the central honesty guarantee of this tool.
    A derived value must never claim a stronger status than the weakest of the
    inputs it was computed from.

    Parameters
    ----------
    data : dict
        The cost model.
    result : ValidationResult
        Accumulator to record issues into.
    """
    assumptions = data.get("assumptions", {})
    assumptions = assumptions if isinstance(assumptions, dict) else {}
    power_status = _status_of(assumptions.get("average_power_draw_watts"))
    price_status = _status_of(assumptions.get("electricity_price_usd_per_kwh"))
    grid_status = _status_of(assumptions.get("grid_carbon_intensity_gco2e_per_kwh"))

    for index, scenario in enumerate(normalize_scenarios(data)):
        label = scenario.get("name") or f"scenario[{index}]"
        runtime_status = _status_of(scenario.get("runtime_seconds"))
        compute = scenario.get("local_compute", {})
        compute = compute if isinstance(compute, dict) else {}

        _error_if_overclaims(
            result,
            label,
            "energy_kwh",
            compute.get("energy_kwh"),
            weakest(runtime_status, power_status),
        )
        energy_status = _status_of(compute.get("energy_kwh"))
        _error_if_overclaims(
            result,
            label,
            "electricity_cost_usd",
            compute.get("electricity_cost_usd"),
            weakest(energy_status, price_status),
        )
        _error_if_overclaims(
            result,
            label,
            "carbon_gco2e",
            compute.get("carbon_gco2e"),
            weakest(energy_status, grid_status),
        )


def _error_if_overclaims(
    result: ValidationResult,
    scenario_label: str,
    field_name: str,
    derived: Any,
    ceiling: str | None,
) -> None:
    """Error when a derived value's status is stronger than its input ceiling.

    Parameters
    ----------
    result : ValidationResult
        Accumulator to record the error into.
    scenario_label : str
        Human label of the scenario, for the message.
    field_name : str
        Name of the derived field being checked.
    derived : Any
        The derived quantity mapping (or anything, handled defensively).
    ceiling : str or None
        The weakest input status the derived value may claim. ``None`` means no
        inputs were declared, so there is nothing to enforce.
    """
    derived_status = _status_of(derived)
    if derived_status is None or ceiling is None:
        return
    if status_strength(derived_status) > status_strength(ceiling):
        result.add(
            "error",
            f"{scenario_label}.{field_name} claims {derived_status!r} but its inputs "
            f"are only {ceiling!r}; a derived value cannot outrank its weakest input.",
        )


def validate_model(data: dict[str, Any]) -> ValidationResult:
    """Validate a cost model and return the accumulated verdict.

    Parameters
    ----------
    data : dict
        The parsed cost model.

    Returns
    -------
    ValidationResult
        Errors and warnings found. The model is valid when it has no errors;
        warnings never fail it, because an honest but imperfect model must load.

    Examples
    --------
    >>> model = {
    ...     "schema_version": "1.0",
    ...     "date_updated": "2026-07-27",
    ...     "canonical_unit_of_work": {"name": "one run", "status": "estimated"},
    ...     "deployment": {"provider": "local"},
    ...     "scenario": {"name": "default"},
    ... }
    >>> validate_model(model).is_valid()
    True
    """
    result = ValidationResult()
    _check_schema_version(data, result)
    _check_required_fields(data, result)
    _check_statuses_and_freshness(data, result)
    _check_numeric_quantities(data, result)
    _check_provenance(data, result)
    _check_weakest_link(data, result)
    return result
