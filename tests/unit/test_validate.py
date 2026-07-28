"""Tests for the validation use case: schema gate, structure, honesty rules."""

from __future__ import annotations

import copy

from cost_running.application import validate_model


def test_valid_model_passes(valid_model):
    result = validate_model(valid_model)
    assert result.is_valid()
    assert not result.errors


def test_missing_required_field_is_an_error(valid_model):
    data = copy.deepcopy(valid_model)
    del data["canonical_unit_of_work"]
    result = validate_model(data)
    assert not result.is_valid()
    assert any("canonical_unit_of_work" in i.message for i in result.errors)


def test_model_without_scenario_is_rejected(valid_model):
    data = copy.deepcopy(valid_model)
    del data["scenario"]
    result = validate_model(data)
    assert not result.is_valid()
    assert any("scenario" in i.message for i in result.errors)


def test_missing_schema_version_warns_but_stays_valid(valid_model):
    data = copy.deepcopy(valid_model)
    del data["schema_version"]
    result = validate_model(data)
    # Legacy and hand-written files must still load, with a nudge.
    assert result.is_valid()
    assert any("schema_version is missing" in i.message for i in result.warnings)


def test_future_major_schema_version_is_rejected(valid_model):
    data = copy.deepcopy(valid_model)
    data["schema_version"] = "2.0"
    result = validate_model(data)
    assert not result.is_valid()
    assert any("newer than this build" in i.message for i in result.errors)


def test_invalid_status_label_is_an_error(valid_model):
    data = copy.deepcopy(valid_model)
    data["scenario"]["runtime_seconds"]["status"] = "guessed"
    result = validate_model(data)
    assert not result.is_valid()
    assert any("invalid status" in i.message for i in result.errors)


def test_weakest_link_is_an_error_when_derived_outranks_inputs(valid_model):
    data = copy.deepcopy(valid_model)
    # Energy claims measured while its power input is only estimated: dishonest.
    data["scenario"]["local_compute"]["energy_kwh"]["status"] = "measured"
    result = validate_model(data)
    # Overclaiming the honesty of a derived value is an error, not merely a warning:
    # it directly contradicts the weakest-link rule, the central honesty guarantee.
    assert not result.is_valid()
    assert any("cannot outrank its weakest input" in i.message for i in result.errors)


def test_numeric_quantity_without_status_is_an_error(valid_model):
    data = copy.deepcopy(valid_model)
    data["scenario"]["runtime_seconds"] = {"value": 1.0, "unit": "s"}  # no status
    result = validate_model(data)
    assert not result.is_valid()
    assert any("no status" in i.message for i in result.errors)


def test_negative_value_is_an_error(valid_model):
    data = copy.deepcopy(valid_model)
    data["scenario"]["runtime_seconds"]["value"] = -1.0
    result = validate_model(data)
    assert not result.is_valid()
    assert any("cannot be negative" in i.message for i in result.errors)


def test_estimated_assumption_without_source_url_warns(valid_model):
    # The conftest fixture has estimated assumptions without source_url; that's
    # an advisory warning (not an error) so the model still loads.
    result = validate_model(valid_model)
    assert result.is_valid()
    assert any("source_url" in i.message for i in result.warnings)


def test_stale_provenance_warns(valid_model):
    data = copy.deepcopy(valid_model)
    # A very old retrieved_date should trip the freshness warning.
    data["assumptions"]["electricity_price_usd_per_kwh"]["retrieved_date"] = "2000-01-01"
    result = validate_model(data)
    assert any("provenance" in i.message and "old" in i.message for i in result.warnings)
