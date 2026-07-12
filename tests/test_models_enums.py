"""Enum value contracts. The string values are persisted in `decision.md` and
templated into prompts, so they're part of the schema, not implementation detail."""

from angel_memos.models import Conviction, Stage, ValuationMethod, Verdict


def test_stage_has_canonical_members() -> None:
    assert {s.value for s in Stage} == {
        "pre_seed",
        "seed",
        "series_a",
        "series_b",
        "series_c",
        "growth",
    }


def test_verdict_has_canonical_members() -> None:
    assert {v.value for v in Verdict} == {"strong_buy", "buy", "hold", "pass"}


def test_conviction_has_canonical_members() -> None:
    assert {c.value for c in Conviction} == {"low", "medium", "high"}


def test_valuation_method_has_canonical_members() -> None:
    assert {m.value for m in ValuationMethod} == {
        "arr_multiple",
        "revenue_ebitda",
        "revenue_pe",
        "gmv_take",
        "seed_outcome",
        "custom",
    }
