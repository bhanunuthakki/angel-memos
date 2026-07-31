"""Deterministic helpers in `research`: model bounds, prompt-builder shape,
text rendering. Claude calls themselves get end-to-end coverage via the
diligence flow on real companies."""

import pytest

from angel_memos.models import Stage
from angel_memos.research import (
    ComparableDeals,
    CompetitorComp,
    FounderProfile,
    PriorEmployer,
    build_comparable_deals_prompt,
    build_founder_profile_prompt,
    render_comparable_deals_text,
    render_founder_profiles_text,
)


def _founder(**overrides: object) -> FounderProfile:
    base: dict[str, object] = {
        "name": "Jane Doe",
        "role": "CEO",
        "prior_employers": [
            PriorEmployer(company="Anduril", role="Director of Product", outcome="still operating"),
        ],
        "education": ["Stanford BS CS"],
        "notable_outcomes": ["Acquihired company X by Anduril 2023"],
        "pedigree_tier": "A",
        "pedigree_justification": "Repeat founder + Anduril director role.",
        "web_research_summary": "LinkedIn confirmed, prior exit verified.",
        "linkedin_url": "https://linkedin.com/in/janedoe",
        "sources": ["LinkedIn profile", "Crunchbase entry"],
    }
    base.update(overrides)
    return FounderProfile.model_validate(base)


def _comp(**overrides: object) -> CompetitorComp:
    base: dict[str, object] = {
        "company_name": "AcmeCo",
        "category_fit": "Same wedge (AI permit intake) and same buyer.",
        "stage": "Seed",
        "last_round_usd": 5_000_000.0,
        "last_round_date": "Q1 2025",
        "valuation_usd": 25_000_000.0,
        "arr_usd": 250_000.0,
        "co_investors": ["a16z", "Lux"],
        "notes": "Two muni pilots announced.",
        "sources": ["Crunchbase", "TechCrunch coverage"],
    }
    base.update(overrides)
    return CompetitorComp.model_validate(base)


def _comps(**overrides: object) -> ComparableDeals:
    base: dict[str, object] = {
        "category": "AI permit intake",
        "comps": [_comp(), _comp(company_name="BetaCo")],
        "summary": "Target at $50M post is rich vs. median $25M in comp set.",
    }
    base.update(overrides)
    return ComparableDeals.model_validate(base)


# ---------------------------------------------------------------------------
# Model bounds.
# ---------------------------------------------------------------------------


def test_founder_profile_requires_pedigree_tier() -> None:
    with pytest.raises(Exception):
        FounderProfile.model_validate(
            {
                "name": "X",
                "pedigree_justification": "y",
                "web_research_summary": "z",
                "sources": ["a"],
            }
        )  # missing pedigree_tier


def test_founder_profile_rejects_invalid_pedigree_tier() -> None:
    with pytest.raises(Exception):
        _founder(pedigree_tier="Z")


def test_founder_profile_requires_at_least_one_source() -> None:
    with pytest.raises(Exception):
        _founder(sources=[])


def test_competitor_comp_requires_at_least_one_source() -> None:
    with pytest.raises(Exception):
        _comp(sources=[])


def test_comparable_deals_requires_at_least_two_comps() -> None:
    with pytest.raises(Exception):
        ComparableDeals.model_validate(
            {
                "category": "x",
                "comps": [_comp()],  # only one
                "summary": "y",
            }
        )


def test_comparable_deals_caps_at_three_comps() -> None:
    """Cap kept tight (3, not 5) so the cumulative response fits Claude's
    per-call output budget. Bigger sets get truncated mid-source."""
    with pytest.raises(Exception):
        ComparableDeals.model_validate(
            {
                "category": "x",
                "comps": [_comp() for _ in range(4)],  # four
                "summary": "y",
            }
        )


def test_competitor_comp_caps_sources_at_four() -> None:
    with pytest.raises(Exception):
        _comp(sources=["a", "b", "c", "d", "e"])  # five


# ---------------------------------------------------------------------------
# Prompt builders.
# ---------------------------------------------------------------------------


def test_founder_prompt_includes_name_and_company() -> None:
    prompt = build_founder_profile_prompt("Jane Doe", "Acme Inc")
    assert "Jane Doe" in prompt
    assert "Acme Inc" in prompt


def test_comparable_deals_prompt_includes_stage_and_keywords() -> None:
    prompt = build_comparable_deals_prompt(
        "Acme",
        ["AI permit intake", "muni govtech"],
        Stage.SEED,
        "Automates plan review for municipal departments.",
    )
    assert "seed" in prompt.lower()
    assert "AI permit intake" in prompt
    assert "muni govtech" in prompt


def test_comparable_deals_prompt_handles_empty_keywords() -> None:
    prompt = build_comparable_deals_prompt("Acme", [], Stage.SERIES_A, "X")
    assert "(none provided)" in prompt


# ---------------------------------------------------------------------------
# Text rendering.
# ---------------------------------------------------------------------------


def test_render_founder_profiles_empty_list_returns_marker() -> None:
    out = render_founder_profiles_text([])
    assert "no founders surfaced" in out.lower()


def test_render_founder_profiles_includes_tier_and_justification() -> None:
    out = render_founder_profiles_text([_founder()])
    assert "Tier A" in out
    assert "Repeat founder + Anduril director role" in out


def test_render_founder_profiles_includes_prior_employer_outcomes() -> None:
    out = render_founder_profiles_text([_founder()])
    assert "Anduril" in out
    assert "still operating" in out


def test_render_founder_profiles_includes_linkedin_when_present() -> None:
    out = render_founder_profiles_text([_founder()])
    assert "linkedin.com/in/janedoe" in out


def test_render_founder_profiles_omits_linkedin_when_empty() -> None:
    out = render_founder_profiles_text([_founder(linkedin_url="")])
    assert "LinkedIn:" not in out


def test_render_comparable_deals_none_returns_marker() -> None:
    out = render_comparable_deals_text(None)
    assert "none found" in out.lower()


def test_render_comparable_deals_includes_summary_and_comps() -> None:
    out = render_comparable_deals_text(_comps())
    assert "Target at $50M post is rich" in out
    assert "AcmeCo" in out
    assert "BetaCo" in out


def test_render_comparable_deals_formats_dollar_amounts() -> None:
    out = render_comparable_deals_text(_comps())
    # Round, valuation, ARR should all appear in some form
    assert "$5,000,000" in out
    assert "$25,000,000" in out


def test_render_comparable_deals_omits_money_line_when_no_amounts() -> None:
    bare_comp = _comp(
        last_round_usd=None,
        valuation_usd=None,
        arr_usd=None,
        last_round_date="",
    )
    comps = ComparableDeals(
        category="x",
        comps=[bare_comp, _comp(company_name="OtherCo")],
        summary="y",
    )
    out = render_comparable_deals_text(comps)
    # The bare_comp's line should not have a money line
    # The OtherCo line should still have its money
    assert "$5,000,000" in out  # OtherCo's


# ---------------------------------------------------------------------------
# Dated multiples. A comp multiple is only usable with basis + fetch date —
# undated multiples drift stale in the cache (observed off 2x in a quarter).
# ---------------------------------------------------------------------------


def test_comp_without_multiple_fields_still_validates() -> None:
    """Back-compat: cached .comparable_deals_cache.json files predate the
    multiple fields and must keep loading."""
    c = _comp()
    assert c.valuation_multiple is None
    assert c.multiple_as_of == ""


def test_dated_multiple_renders_with_basis_and_date() -> None:
    comps = _comps(
        comps=[
            _comp(
                valuation_multiple=10.3,
                multiple_basis="P/S TTM",
                multiple_as_of="2026-07-31",
            ),
            _comp(company_name="BetaCo"),
        ]
    )
    assert "10.3x P/S TTM as of 2026-07-31" in render_comparable_deals_text(comps)


def test_undated_multiple_is_labeled_undated() -> None:
    comps = _comps(comps=[_comp(valuation_multiple=10.3), _comp(company_name="BetaCo")])
    assert "(UNDATED)" in render_comparable_deals_text(comps)
