"""AngelListMetadata captures the structured TERMS-table fields from an AL
memo. Narrative sections are passed as raw text elsewhere and are not part
of this schema."""

from datetime import date

import pytest
from pydantic import ValidationError

from angel_memos.models import AngelListMetadata, Stage


def _spotai_kwargs() -> dict[str, object]:
    """Mirrors the SpotAI AL Details PDF TERMS table."""
    return {
        "company": "Spot AI",
        "round_label": "Series B+",
        "stage": Stage.SERIES_B.value,
        "instrument": "Equity",
        "estimated_round_size_usd": 20_000_000,
        "share_class": "Preferred",
        "pre_money_usd": 175_000_000,
        "allocation_usd": 63_000,
        "estimated_expenses_pct": 0.111,
        "leads_investment_usd": 10_000,
        "gross_carry_pct": 0.20,
        "min_investment_usd": 2_000,
        "deadline": date(2025, 11, 20),
        "markets": ["AI", "ML"],
        "founders": ["Rish Gupta", "Sud Bhatija", "Tanuj Thapliyal"],
        "co_investors": ["Scale Venture Partners", "Qualcomm Ventures", "StepStone Group"],
        "total_prior_capital_usd": 93_000_000,
    }


def test_angellist_metadata_round_trip() -> None:
    m = AngelListMetadata.model_validate(_spotai_kwargs())
    assert m.company == "Spot AI"
    assert m.stage == Stage.SERIES_B
    assert m.markets == ["AI", "ML"]
    assert len(m.founders) == 3


def test_angellist_metadata_derives_post_money() -> None:
    m = AngelListMetadata.model_validate(_spotai_kwargs())
    assert m.post_money_usd == 195_000_000


def test_angellist_metadata_allows_missing_deadline() -> None:
    """Some live deals have no published deadline."""
    kwargs = _spotai_kwargs()
    kwargs["deadline"] = None
    m = AngelListMetadata.model_validate(kwargs)
    assert m.deadline is None


def test_angellist_metadata_allows_empty_co_investors() -> None:
    """Some deals don't surface co-investors."""
    kwargs = _spotai_kwargs()
    kwargs["co_investors"] = []
    m = AngelListMetadata.model_validate(kwargs)
    assert m.co_investors == []


def test_angellist_metadata_allows_missing_prior_capital() -> None:
    """Pre-seed deals usually have no prior raise."""
    kwargs = _spotai_kwargs()
    kwargs["total_prior_capital_usd"] = None
    m = AngelListMetadata.model_validate(kwargs)
    assert m.total_prior_capital_usd is None


def test_angellist_metadata_requires_at_least_one_market() -> None:
    with pytest.raises(ValidationError):
        AngelListMetadata.model_validate({**_spotai_kwargs(), "markets": []})


def test_angellist_metadata_allows_empty_founders() -> None:
    """Some AL-only deals don't surface the team in the memo body. Diligence
    flags this as a gap and may recover via web search."""
    m = AngelListMetadata.model_validate({**_spotai_kwargs(), "founders": []})
    assert m.founders == []


def test_angellist_metadata_rejects_carry_above_one() -> None:
    with pytest.raises(ValidationError):
        AngelListMetadata.model_validate({**_spotai_kwargs(), "gross_carry_pct": 1.5})


def test_angellist_metadata_round_trips_through_json() -> None:
    """The model includes a computed `post_money_usd` field that JSON-dump
    emits but the schema shouldn't accept as user input. `extra="ignore"`
    lets the cache roundtrip; the computed property recalculates on load."""
    original = AngelListMetadata.model_validate(_spotai_kwargs())
    reloaded = AngelListMetadata.model_validate_json(original.model_dump_json())
    assert reloaded.post_money_usd == original.post_money_usd
    assert reloaded.company == original.company


def test_angellist_metadata_ignores_unknown_fields() -> None:
    """Unknown user-supplied fields are silently dropped rather than raising
    (a tradeoff accepted so the JSON-round-trip use case works)."""
    m = AngelListMetadata.model_validate({**_spotai_kwargs(), "unknown_field": "oops"})
    assert not hasattr(m, "unknown_field")
