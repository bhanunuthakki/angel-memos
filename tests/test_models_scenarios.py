"""Per-valuation-method scenario types. Each has shared (`name`,
`probability`, `future_dilution`) plus method-specific drivers."""

import pytest
from pydantic import ValidationError

from angel_memos.models import (
    ArrScenario,
    GmvScenario,
    RevenueEbitdaScenario,
    RevenuePeScenario,
    SeedScenario,
)


def test_arr_scenario_round_trip() -> None:
    s = ArrScenario(
        name="Base",
        probability=0.3,
        future_dilution=0.35,
        cagr=0.45,
        exit_multiple=10.0,
    )
    assert s.cagr == 0.45
    assert s.exit_multiple == 10.0


def test_revenue_ebitda_scenario_round_trip() -> None:
    s = RevenueEbitdaScenario(
        name="Base",
        probability=0.3,
        future_dilution=0.35,
        revenue_cagr=0.4,
        terminal_ebitda_margin=0.25,
        ev_ebitda=18.0,
    )
    assert s.ev_ebitda == 18.0


def test_revenue_pe_scenario_round_trip() -> None:
    s = RevenuePeScenario(
        name="Base",
        probability=0.3,
        future_dilution=0.35,
        revenue_cagr=0.4,
        terminal_net_margin=0.18,
        pe_ratio=22.0,
    )
    assert s.pe_ratio == 22.0


def test_gmv_scenario_round_trip() -> None:
    s = GmvScenario(
        name="Base",
        probability=0.3,
        future_dilution=0.35,
        gmv_cagr=0.5,
        take_rate=0.08,
        revenue_multiple=6.0,
    )
    assert s.take_rate == 0.08


def test_seed_scenario_round_trip() -> None:
    s = SeedScenario(
        name="breakout",
        probability=0.1,
        future_dilution=0.5,
        exit_value_usd=2_000_000_000,
    )
    assert s.exit_value_usd == 2_000_000_000


def test_seed_scenario_rejects_unknown_name() -> None:
    with pytest.raises(ValidationError):
        SeedScenario.model_validate(
            {
                "name": "moonshot",
                "probability": 0.1,
                "future_dilution": 0.5,
                "exit_value_usd": 1_000_000_000,
            }
        )


def test_scenario_rejects_probability_above_one() -> None:
    with pytest.raises(ValidationError):
        ArrScenario(
            name="Base",
            probability=1.5,
            future_dilution=0.35,
            cagr=0.45,
            exit_multiple=10.0,
        )


def test_scenario_rejects_negative_probability() -> None:
    with pytest.raises(ValidationError):
        ArrScenario(
            name="Base",
            probability=-0.1,
            future_dilution=0.35,
            cagr=0.45,
            exit_multiple=10.0,
        )


def test_scenario_rejects_dilution_at_or_above_one() -> None:
    with pytest.raises(ValidationError):
        ArrScenario(
            name="Base",
            probability=0.3,
            future_dilution=1.0,
            cagr=0.45,
            exit_multiple=10.0,
        )


def test_scenario_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ArrScenario.model_validate(
            {
                "name": "Base",
                "probability": 0.3,
                "future_dilution": 0.35,
                "cagr": 0.45,
                "exit_multiple": 10.0,
                "unknown_field": "oops",
            }
        )
