"""Shared fixtures for the cost-running test suite."""

from __future__ import annotations

import pytest


@pytest.fixture
def valid_model() -> dict:
    """Return a minimal, schema-valid, honest cost model.

    The values are internally consistent under the weakest-link rule: the derived
    energy/cost/carbon are ``estimated``, matching their estimated inputs.
    """
    return {
        "schema_version": "1.0",
        "date_updated": "2026-07-27",
        "canonical_unit_of_work": {"name": "one run", "status": "estimated"},
        "deployment": {"provider": "local", "country": "FR"},
        "assumptions": {
            "average_power_draw_watts": {"value": 15, "unit": "W", "status": "estimated"},
            "electricity_price_usd_per_kwh": {
                "value": 0.28,
                "unit": "USD/kWh",
                "status": "estimated",
            },
            "grid_carbon_intensity_gco2e_per_kwh": {
                "value": 55,
                "unit": "gCO2e/kWh",
                "status": "estimated",
            },
        },
        "scenario": {
            "name": "default",
            "runtime_seconds": {"value": 1.0, "unit": "s", "status": "measured"},
            "local_compute": {
                "energy_kwh": {"value": 4.2e-6, "status": "estimated"},
                "electricity_cost_usd": {"value": 1.2e-6, "status": "estimated"},
                "carbon_gco2e": {"value": 2.3e-4, "status": "estimated"},
            },
            "external_api_cost_usd": {"value": 0.0, "unit": "USD", "status": "placeholder"},
            "totals": {
                "total_cost_usd": {"value": 1.2e-6, "status": "estimated"},
                "total_carbon_gco2e": {"value": 2.3e-4, "status": "estimated"},
            },
        },
    }
