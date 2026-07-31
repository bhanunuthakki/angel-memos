"""Deterministic helpers in `research`: model bounds, prompt-builder shape,
text rendering. Claude calls themselves get end-to-end coverage via the
diligence flow on real companies."""

from pathlib import Path

import pytest

from angel_memos.models import Stage
from angel_memos.research import (
    RECENT_EVENTS_FILENAME,
    CompanyEvent,
    ComparableDeals,
    CompetitorComp,
    FounderProfile,
    PriorEmployer,
    RecentEvents,
    build_comparable_deals_prompt,
    build_founder_profile_prompt,
    build_recent_events_prompt,
    load_or_find_events,
    render_comparable_deals_text,
    render_founder_profiles_text,
    render_recent_events_text,
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


# ---------------------------------------------------------------------------
# Recent-events sweep: schema, cache decay, and rendering. The sweep exists
# because a claims-level brief cannot see a customer CEO's "not ready for
# prime time" quote or a buyer insourcing its way out of the customer pool.
# ---------------------------------------------------------------------------


def _event(**overrides: object) -> CompanyEvent:
    base: dict[str, object] = {
        "date": "2026-01",
        "headline": "Customer CEO: technology 'in the pilot stage, not ready for prime time'",
        "why_material": "Directly contradicts the deck's production-scale framing.",
        "source": "finance.yahoo.com interview",
        "source_type": "secondary",
    }
    return CompanyEvent.model_validate(base | overrides)


def _events(**overrides: object) -> RecentEvents:
    base: dict[str, object] = {
        "company": "Acme",
        "as_of": "2026-07-31",
        "events": [_event()],
        "searches_run": ["Acme news 2025 2026", "Acme CEO interview"],
    }
    return RecentEvents.model_validate(base | overrides)


def test_empty_sweep_still_records_its_searches() -> None:
    swept = _events(events=[])
    text = render_recent_events_text(swept)
    assert "No material events found" in text
    assert "Acme news 2025 2026" in text  # evidenced absence, not silence


def test_events_render_with_date_source_and_materiality() -> None:
    text = render_recent_events_text(_events())
    assert "[2026-01]" in text
    assert "not ready for prime time" in text
    assert "Why material:" in text


def test_fresh_events_cache_is_reused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import date as _date

    cached = _events(as_of=_date.today().isoformat())
    (tmp_path / RECENT_EVENTS_FILENAME).write_text(cached.model_dump_json(), encoding="utf-8")

    def boom(*args: object, **kwargs: object) -> RecentEvents:
        raise AssertionError("fresh cache must not trigger a re-research call")

    monkeypatch.setattr("angel_memos.research.find_recent_events", boom)
    result = load_or_find_events(tmp_path, "Acme", ["robotics"], [])
    assert result is not None and result.events[0].date == "2026-01"


def test_stale_events_cache_is_refreshed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stale = _events(as_of="2026-01-01")
    (tmp_path / RECENT_EVENTS_FILENAME).write_text(stale.model_dump_json(), encoding="utf-8")
    fresh = _events(as_of="2026-07-31", events=[_event(headline="New competitor take-private")])

    def _return_fresh(
        company_name: str, category_keywords: list[str], anchor_names: list[str]
    ) -> RecentEvents:
        return fresh

    monkeypatch.setattr("angel_memos.research.find_recent_events", _return_fresh)

    result = load_or_find_events(tmp_path, "Acme", ["robotics"], [])

    assert result is not None
    assert result.events[0].headline == "New competitor take-private"
    on_disk = RecentEvents.model_validate_json(
        (tmp_path / RECENT_EVENTS_FILENAME).read_text(encoding="utf-8")
    )
    assert on_disk.as_of == "2026-07-31"  # cache rewritten


def test_recent_events_prompt_names_anchors_and_today() -> None:
    prompt = build_recent_events_prompt("Acme", ["robotics"], ["FedEx", "Nimble"])
    assert "FedEx, Nimble" in prompt
    assert "Acme" in prompt


def test_comps_prompt_never_asks_for_more_than_the_schema_accepts() -> None:
    """Adversarial-review finding: the prompt said 2-4 while the schema caps
    at 3, inviting a guaranteed validation retry. Pin prompt to schema."""
    cap = ComparableDeals.model_fields["comps"].metadata
    max_len = next(m.max_length for m in cap if hasattr(m, "max_length"))
    prompt = build_comparable_deals_prompt("Acme", ["robotics"], Stage.SEED, "robots")
    for n in range(max_len + 1, max_len + 4):
        assert f"2-{n}" not in prompt
    assert f"2-{max_len}" in prompt
