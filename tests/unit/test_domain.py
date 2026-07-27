"""Tests for the domain layer: taxonomy, dimensions, quantity, schema."""

from __future__ import annotations

import pytest

from cost_running.domain import (
    CANONICAL_DIMENSIONS,
    SCHEMA_VERSION,
    Dimension,
    DimensionRegistry,
    Quantity,
    is_valid_status,
    schema_major,
    status_strength,
    weakest,
)


def test_status_strength_orders_measured_above_todo():
    # The whole weakest-link rule rests on this ordering.
    assert status_strength("measured") > status_strength("estimated") > status_strength("TODO")


def test_unknown_status_is_weaker_than_todo():
    # A typo must never let a derived value claim more confidence than it earned.
    assert status_strength("guessed") < status_strength("TODO")
    assert not is_valid_status("guessed")


def test_weakest_ignores_none_and_returns_lowest():
    assert weakest("measured", "estimated") == "estimated"
    assert weakest("measured", None, "TODO") == "TODO"
    assert weakest(None, None) is None


def test_canonical_registry_has_the_five_dimensions():
    registry = DimensionRegistry()
    keys = registry.keys()
    assert keys == ("money", "time", "energy", "carbon", "water")
    assert len(CANONICAL_DIMENSIONS) == 5


def test_registry_registers_new_dimension_and_rejects_duplicate():
    registry = DimensionRegistry()
    registry.register(Dimension("egress", "Egress", "GB", "Bytes leaving the boundary."))
    assert "egress" in registry
    assert registry.get("egress").unit == "GB"
    # A duplicate key would make report order ambiguous; it must be refused.
    with pytest.raises(ValueError):
        registry.register(Dimension("energy", "Energy", "kWh", "duplicate"))


def test_quantity_round_trips_through_mapping():
    mapping = {"value": 0.16, "unit": "USD/kWh", "status": "estimated"}
    q = Quantity.from_mapping(mapping)
    assert (q.value, q.unit, q.status) == (0.16, "USD/kWh", "estimated")
    assert q.is_status_valid()
    # Round-tripping a minimal quantity does not sprout empty optional keys.
    assert q.to_mapping() == mapping


def test_schema_major_parses_and_rejects():
    assert schema_major(SCHEMA_VERSION) == 1
    assert schema_major("2.3") == 2
    assert schema_major("abc") is None
