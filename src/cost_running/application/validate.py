"""
Validate a cost model against the schema and the honesty rules.

Module summary
--------------
Validation is where the honesty taxonomy stops being a convention and becomes
enforceable. This use case checks four families of rule: the schema version is
one this build understands, the required structure is present, every status is a
real label, and no derived value claims to be better-founded than its inputs (the
weakest-link rule). It returns a :class:`ValidationResult`; it never prints or
exits, so every delivery surface reports the verdict its own way.

Author
------
Project maintainers.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from ..domain.schema import SCHEMA_VERSION, schema_major
from ..domain.taxonomy import is_valid_status, status_strength, weakest
from .results import ValidationResult

# A model whose provenance was retrieved more than this many days ago earns a
# freshness warning: tariffs and grid intensities drift, and a stale source is a
# quiet way for a number to become wrong.
_FRESHNESS_WARN_DAYS: int = 90


def _status_of(obj: Any) -> str | None:
    """Return the ``status`` of a quantity-shaped mapping, or ``None``.

    Parameters
    ----------
    obj : Any
        Any value; only a mapping carrying a ``status`` key yields a status.

    Returns
    -------
    str or None
        The status string, or ``None`` when ``obj`` is not a quantity mapping.
    """
    if isinstance(obj, dict) and "status" in obj:
        return str(obj["status"])
    return None


def _is_date_string(value: str) -> bool:
    """Return whether ``value`` is a valid ``YYYY-MM-DD`` calendar date.

    Parameters
    ----------
    value : str
        The candidate date string.

    Returns
    -------
    bool
        ``True`` when it parses as an ISO calendar date.
    """
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        # fromisoformat rejects both wrong shapes and impossible dates (2026-13-40).
        return False


def _days_since(value: str) -> int | None:
    """Return the number of days between ``value`` and today, or ``None``.

    Parameters
    ----------
    value : str
        A ``YYYY-MM-DD`` date string.

    Returns
    -------
    int or None
        Days elapsed, or ``None`` when the string is not a valid date.
    """
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
    # Prefer the explicit multi-scenario form when present.
    if isinstance(data.get("scenarios"), list):
        return [s for s in data["scenarios"] if isinstance(s, dict)]
    if isinstance(data.get("scenario"), dict):
        return [data["scenario"]]
    return []


def _check_schema_version(data: dict[str, Any], result: ValidationResult) -> None:
    """Apply the schema-version compatibility policy.

    Parameters
    ----------
    data : dict
        The cost model.
    result : ValidationResult
        Accumulator to record issues into.
    """
    # A missing version is tolerated so hand-written and legacy files still load,
    # but nudged toward declaring one.
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
        # A malformed version is a hard error: we cannot reason about compatibility.
        result.add(
            "error",
            f'schema_version must be a MAJOR.MINOR string, e.g. "{SCHEMA_VERSION}".',
        )
    elif declared > current:
        # A newer major uses rules this build does not have; refuse to misread it.
        result.add(
            "error",
            f"schema_version {data['schema_version']!r} is newer than this build "
            "supports (1.x); upgrade cost-running.",
        )
    elif declared < current:
        # An older major still loads, with a nudge to refresh.
        result.add(
            "warning",
            f"schema_version {data['schema_version']!r} predates the locked 1.0 "
            f'schema; re-audit or bump it to "{SCHEMA_VERSION}".',
        )


def _check_required_fields(data: dict[str, Any], result: ValidationResult) -> None:
    """Verify the mandatory top-level structure is present and well-formed.

    Parameters
    ----------
    data : dict
        The cost model.
    result : ValidationResult
        Accumulator to record issues into.
    """
    # These three containers must exist for a model to mean anything.
    for field_name in ("date_updated", "canonical_unit_of_work", "deployment"):
        if field_name not in data:
            result.add("error", f"Missing required top-level field: {field_name}")

    # date_updated must be a real date, and its template placeholder is a warning
    # so a freshly scaffolded model reminds the author to set it.
    if "date_updated" in data:
        updated = str(data["date_updated"])
        if updated == "YYYY-MM-DD":
            result.add("warning", "date_updated is still a template placeholder.")
        elif not _is_date_string(updated):
            result.add("error", "date_updated must be a YYYY-MM-DD calendar date.")

    # The canonical unit of work needs a concrete name and a valid status; a model
    # without a named unit answers no question.
    cuow = data.get("canonical_unit_of_work")
    if isinstance(cuow, dict):
        if not cuow.get("name"):
            result.add("error", "canonical_unit_of_work.name is required.")
        status = _status_of(cuow)
        if status is not None and not is_valid_status(status):
            result.add("error", f"canonical_unit_of_work has invalid status {status!r}.")
    elif "canonical_unit_of_work" in data:
        result.add("error", "canonical_unit_of_work must be a mapping.")

    # Every model must describe at least one scenario.
    if not normalize_scenarios(data):
        result.add("error", "A model must define a `scenario` or a `scenarios` list.")


def _check_statuses_and_freshness(data: dict[str, Any], result: ValidationResult) -> None:
    """Check every quantity's status label and provenance freshness.

    Walks the whole model recursively so a status typo anywhere is caught, and
    warns when a sourced value's ``retrieved_date`` is stale.

    Parameters
    ----------
    data : dict
        The cost model.
    result : ValidationResult
        Accumulator to record issues into.
    """

    def walk(node: Any, path: str) -> None:
        """Recurse into mappings and lists, checking any quantity found."""
        if isinstance(node, dict):
            # A mapping carrying a status is a quantity: validate its label and
            # the freshness of its provenance.
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
            # Recurse into children to reach nested quantities.
            for key, child in node.items():
                walk(child, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{path}[{index}]")

    walk(data, "")


def _check_weakest_link(data: dict[str, Any], result: ValidationResult) -> None:
    """Warn when a derived value outranks the honesty of its inputs.

    Enforces the weakest-link rule for the three classic derivations:
    energy from runtime and power, money from energy and price, carbon from
    energy and grid intensity. A derived value may claim at most the status of
    its weakest input.

    Parameters
    ----------
    data : dict
        The cost model.
    result : ValidationResult
        Accumulator to record issues into.
    """
    assumptions = data.get("assumptions", {})
    assumptions = assumptions if isinstance(assumptions, dict) else {}
    # The three assumption inputs that feed the derived scenario values.
    power_status = _status_of(assumptions.get("average_power_draw_watts"))
    price_status = _status_of(assumptions.get("electricity_price_usd_per_kwh"))
    grid_status = _status_of(assumptions.get("grid_carbon_intensity_gco2e_per_kwh"))

    for index, scenario in enumerate(normalize_scenarios(data)):
        label = scenario.get("name") or f"scenario[{index}]"
        runtime_status = _status_of(scenario.get("runtime_seconds"))
        compute = scenario.get("local_compute", {})
        compute = compute if isinstance(compute, dict) else {}

        # energy = runtime * power: cannot beat the weaker of the two.
        _warn_if_overclaims(
            result,
            label,
            "energy_kwh",
            compute.get("energy_kwh"),
            weakest(runtime_status, power_status),
        )
        energy_status = _status_of(compute.get("energy_kwh"))
        # money = energy * price.
        _warn_if_overclaims(
            result,
            label,
            "electricity_cost_usd",
            compute.get("electricity_cost_usd"),
            weakest(energy_status, price_status),
        )
        # carbon = energy * grid intensity.
        _warn_if_overclaims(
            result,
            label,
            "carbon_gco2e",
            compute.get("carbon_gco2e"),
            weakest(energy_status, grid_status),
        )


def _warn_if_overclaims(
    result: ValidationResult,
    scenario_label: str,
    field_name: str,
    derived: Any,
    ceiling: str | None,
) -> None:
    """Warn when a derived value's status is stronger than its input ceiling.

    Parameters
    ----------
    result : ValidationResult
        Accumulator to record the warning into.
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
    # Nothing to compare against if either side is absent.
    if derived_status is None or ceiling is None:
        return
    # A derived value stronger than its weakest input is exactly the dishonesty
    # the rule exists to catch.
    if status_strength(derived_status) > status_strength(ceiling):
        result.add(
            "warning",
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
    # Run the four rule families in order of severity of what they gate on.
    _check_schema_version(data, result)
    _check_required_fields(data, result)
    _check_statuses_and_freshness(data, result)
    _check_weakest_link(data, result)
    return result
