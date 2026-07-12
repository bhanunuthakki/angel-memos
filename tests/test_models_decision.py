"""Decision composite — the schema that `decision.md` parses into and that
`memo` consumes. Validators enforce cross-field consistency: scenario and
benchmark types match `valuation_method`, probabilities sum to 1.0, and exit-
math fields are present iff the decision involves a commitment."""

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from angel_memos.models import (
    ArrScenario,
    Conviction,
    Decision,
    SeedScenario,
    ValuationMethod,
    Verdict,
)


def _arr_scenarios() -> list[dict[str, object]]:
    """Five-scenario set matching the SpotAI sheet's probability split."""
    return [
        {
            "name": "Base",
            "probability": 0.3,
            "future_dilution": 0.35,
            "cagr": 0.45,
            "exit_multiple": 10.0,
        },
        {
            "name": "Aggressive",
            "probability": 0.15,
            "future_dilution": 0.3,
            "cagr": 0.6,
            "exit_multiple": 15.0,
        },
        {
            "name": "Conservative",
            "probability": 0.25,
            "future_dilution": 0.45,
            "cagr": 0.3,
            "exit_multiple": 7.0,
        },
        {
            "name": "Home Run",
            "probability": 0.1,
            "future_dilution": 0.25,
            "cagr": 0.75,
            "exit_multiple": 20.0,
        },
        {
            "name": "Downside",
            "probability": 0.2,
            "future_dilution": 0.6,
            "cagr": 0.1,
            "exit_multiple": 4.0,
        },
    ]


def _arr_benchmarks() -> list[dict[str, object]]:
    return [
        {
            "rank_label": "Top 1",
            "comparable": "Datadog",
            "terminal_arr_usd": 2_500_000_000,
            "exit_multiple": 16.0,
            "exit_valuation_usd": 40_000_000_000,
        },
        {
            "rank_label": "Top 5",
            "comparable": "Snowflake",
            "terminal_arr_usd": 2_000_000_000,
            "exit_multiple": 14.0,
            "exit_valuation_usd": 28_000_000_000,
        },
        {
            "rank_label": "Top 20",
            "comparable": "Sumo Logic",
            "terminal_arr_usd": 250_000_000,
            "exit_multiple": 6.0,
            "exit_valuation_usd": 1_500_000_000,
        },
    ]


def _arr_decision_kwargs() -> dict[str, object]:
    return {
        "company": "Spot AI",
        "verdict": Verdict.BUY.value,
        "conviction": Conviction.HIGH.value,
        "check_usd": 10_000,
        "post_money_usd": 195_000_000,
        "valuation_method": ValuationMethod.ARR_MULTIPLE.value,
        "current_base_metric_usd": 23_000_000,
        "scenarios": _arr_scenarios(),
        "benchmarks": _arr_benchmarks(),
        "top_reasons": [
            "AI-native architecture",
            "Strong founder track record",
            "Massive video data tailwind",
        ],
        "top_risks": [
            "Verkada/Axis response",
            "Enterprise sales execution",
            "Customer concentration",
        ],
        "raw_reasoning": "High conviction on category timing and team.",
    }


def _seed_decision_kwargs() -> dict[str, object]:
    return {
        "company": "Seed Co",
        "verdict": Verdict.BUY.value,
        "conviction": Conviction.MEDIUM.value,
        "check_usd": 25_000,
        "post_money_usd": 12_000_000,
        "valuation_method": ValuationMethod.SEED_OUTCOME.value,
        "current_base_metric_usd": None,
        "scenarios": [
            {"name": "zero", "probability": 0.5, "future_dilution": 0.4, "exit_value_usd": 0},
            {
                "name": "acqui_hire",
                "probability": 0.2,
                "future_dilution": 0.4,
                "exit_value_usd": 30_000_000,
            },
            {
                "name": "modest",
                "probability": 0.15,
                "future_dilution": 0.55,
                "exit_value_usd": 300_000_000,
            },
            {
                "name": "breakout",
                "probability": 0.1,
                "future_dilution": 0.65,
                "exit_value_usd": 3_000_000_000,
            },
            {
                "name": "generational",
                "probability": 0.05,
                "future_dilution": 0.7,
                "exit_value_usd": 30_000_000_000,
            },
        ],
        "benchmarks": [
            {"rank_label": "Top 1", "comparable": "Datadog", "exit_valuation_usd": 40_000_000_000},
            {
                "rank_label": "Top 5",
                "comparable": "Sumo Logic",
                "exit_valuation_usd": 1_500_000_000,
            },
            {
                "rank_label": "Top 20",
                "comparable": "Generic acqui",
                "exit_valuation_usd": 30_000_000,
            },
        ],
        "top_reasons": ["A", "B", "C"],
        "top_risks": ["D", "E", "F"],
        "raw_reasoning": "Early-stage power-law bet.",
    }


def test_decision_arr_round_trip() -> None:
    d = Decision.model_validate(_arr_decision_kwargs())
    assert d.company == "Spot AI"
    assert d.valuation_method == ValuationMethod.ARR_MULTIPLE
    assert d.scenarios is not None
    assert len(d.scenarios) == 5
    assert d.benchmarks is not None
    assert len(d.benchmarks) == 3
    first = d.scenarios[0]
    assert isinstance(first, ArrScenario)
    assert first.name == "Base"
    assert first.cagr == 0.45


def test_decision_seed_round_trip() -> None:
    d = Decision.model_validate(_seed_decision_kwargs())
    assert d.valuation_method == ValuationMethod.SEED_OUTCOME
    assert d.scenarios is not None
    first = d.scenarios[0]
    assert isinstance(first, SeedScenario)
    assert first.name == "zero"
    assert first.exit_value_usd == 0


def test_decision_pass_omits_exit_math_fields() -> None:
    d = Decision.model_validate(
        {
            "company": "Passed Co",
            "verdict": Verdict.PASS.value,
            "conviction": Conviction.LOW.value,
            "check_usd": 0,
            "post_money_usd": 10_000_000,
            "valuation_method": ValuationMethod.ARR_MULTIPLE.value,
            "current_base_metric_usd": None,
            "scenarios": None,
            "benchmarks": None,
            "top_reasons": ["Burn", "TAM", "Team"],
            "top_risks": ["Tech", "Market", "Exec"],
            "raw_reasoning": "Passed for fund-fit reasons.",
        }
    )
    assert d.verdict == Verdict.PASS
    assert d.scenarios is None
    assert d.benchmarks is None


def test_decision_custom_method_omits_exit_math_fields() -> None:
    d = Decision.model_validate(
        {
            "company": "Custom Co",
            "verdict": Verdict.BUY.value,
            "conviction": Conviction.MEDIUM.value,
            "check_usd": 15_000,
            "post_money_usd": 50_000_000,
            "valuation_method": ValuationMethod.CUSTOM.value,
            "current_base_metric_usd": None,
            "scenarios": None,
            "benchmarks": None,
            "top_reasons": ["A", "B", "C"],
            "top_risks": ["D", "E", "F"],
            "raw_reasoning": "Bespoke deal; hand-modeled.",
        }
    )
    assert d.valuation_method == ValuationMethod.CUSTOM
    assert d.scenarios is None


def _mutate(
    kwargs_factory: Callable[[], dict[str, object]], **overrides: object
) -> dict[str, object]:
    data = kwargs_factory()
    data.update(overrides)
    return data


def test_decision_rejects_probabilities_not_summing_to_one() -> None:
    scenarios = _arr_scenarios()
    scenarios[0]["probability"] = 0.1  # sum becomes 0.8
    with pytest.raises(ValidationError, match=r"probabilit"):
        Decision.model_validate(_mutate(_arr_decision_kwargs, scenarios=scenarios))


def test_decision_rejects_scenario_type_mismatch() -> None:
    """arr_multiple method with seed-shaped scenarios is rejected — the
    dispatcher tries to coerce dicts to ArrScenario and fails on missing fields."""
    seed_shaped: list[dict[str, object]] = [
        {"name": "zero", "probability": 0.2, "future_dilution": 0.4, "exit_value_usd": 0},
        {
            "name": "acqui_hire",
            "probability": 0.2,
            "future_dilution": 0.4,
            "exit_value_usd": 30_000_000,
        },
        {
            "name": "modest",
            "probability": 0.2,
            "future_dilution": 0.55,
            "exit_value_usd": 300_000_000,
        },
        {
            "name": "breakout",
            "probability": 0.2,
            "future_dilution": 0.65,
            "exit_value_usd": 3_000_000_000,
        },
        {
            "name": "generational",
            "probability": 0.2,
            "future_dilution": 0.7,
            "exit_value_usd": 30_000_000_000,
        },
    ]
    with pytest.raises(ValidationError):
        Decision.model_validate(_mutate(_arr_decision_kwargs, scenarios=seed_shaped))


def test_decision_rejects_missing_scenarios_for_committed_decision() -> None:
    with pytest.raises(ValidationError, match=r"scenarios"):
        Decision.model_validate(_mutate(_arr_decision_kwargs, scenarios=None))


def test_decision_rejects_missing_benchmarks_for_committed_decision() -> None:
    with pytest.raises(ValidationError, match=r"benchmark"):
        Decision.model_validate(_mutate(_arr_decision_kwargs, benchmarks=None))


def test_decision_rejects_scenarios_when_verdict_is_pass() -> None:
    with pytest.raises(ValidationError, match=r"(pass|scenarios)"):
        Decision.model_validate(_mutate(_arr_decision_kwargs, verdict=Verdict.PASS.value))


def test_decision_rejects_top_reasons_wrong_count() -> None:
    with pytest.raises(ValidationError):
        Decision.model_validate(_mutate(_arr_decision_kwargs, top_reasons=["A", "B"]))


def test_decision_rejects_top_risks_wrong_count() -> None:
    with pytest.raises(ValidationError):
        Decision.model_validate(_mutate(_arr_decision_kwargs, top_risks=["A", "B", "C", "D"]))


def test_decision_rejects_empty_company() -> None:
    with pytest.raises(ValidationError):
        Decision.model_validate(_mutate(_arr_decision_kwargs, company=""))


def test_decision_requires_current_base_metric_for_growth_methods() -> None:
    with pytest.raises(ValidationError, match=r"current_base_metric"):
        Decision.model_validate(_mutate(_arr_decision_kwargs, current_base_metric_usd=None))
