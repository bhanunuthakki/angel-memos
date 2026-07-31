"""Diligence output structure: the scenario schema's micro-math contract and
the rendered HTML's structural properties. No Claude calls; no assertions on
prompt wording or memo copy."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from angel_memos.diligence import render_diligence_html
from angel_memos.models import (
    DiligenceSection,
    DiligenceTopics,
    ScenarioAnalysisSection,
    ScenarioOutcome,
)


def _outcome(**overrides: object) -> ScenarioOutcome:
    base: dict[str, object] = {
        "name": "Base",
        "probability": 1.0,
        "return_mom": 2.5,
        "key_assumption": "strategic acquisition",
        "derivation": "$90M rev x 5x (Trimble-anchored) = $450M EV / $135M entry x 0.85",
        "exit_value_usd": 450_000_000,
    }
    return ScenarioOutcome.model_validate(base | overrides)


def _section() -> DiligenceSection:
    return DiligenceSection(
        bull_points=["wedge is clean"],
        bear_points=["incumbent copies it"],
        verdict="PASS UNLESS priced flat.",
        kill_conditions=["gross margin <30%"],
    )


def _topics() -> DiligenceTopics:
    scenarios = [
        _outcome(name="Zero", probability=0.3, return_mom=0.0, exit_value_usd=0.0),
        _outcome(name="Base", probability=0.5),
        _outcome(name="Regime change", probability=0.2, return_mom=12.0, exit_value_usd=None),
    ]
    return DiligenceTopics(
        company="Acme",
        top_priorities=["check comp multiple live"],
        business_overview=_section(),
        strategy=_section(),
        value_chain=_section(),
        competitive_landscape=_section(),
        opportunities_and_risks=_section(),
        team=_section(),
        financials=_section(),
        scenario_analysis=ScenarioAnalysisSection(
            bull_points=["upside math holds"],
            bear_points=["downside math dominates"],
            verdict="PASS.",
            kill_conditions=["FY revenue miss >30%"],
            scenarios=scenarios,
        ),
        verdict_and_open_questions=_section(),
    )


# ---------------------------------------------------------------------------
# Scenario schema: the derivation is the contract, not an optional nicety.
# ---------------------------------------------------------------------------


def test_scenario_outcome_requires_a_derivation() -> None:
    with pytest.raises(ValidationError):
        ScenarioOutcome.model_validate(
            {
                "name": "Base",
                "probability": 0.5,
                "return_mom": 2.5,
                "key_assumption": "strategic acquisition",
            }
        )


def test_scenario_outcome_rejects_empty_derivation() -> None:
    with pytest.raises(ValidationError):
        _outcome(derivation="")


def test_exit_value_is_optional_but_never_negative() -> None:
    assert _outcome(exit_value_usd=None).exit_value_usd is None
    with pytest.raises(ValidationError):
        _outcome(exit_value_usd=-1.0)


# ---------------------------------------------------------------------------
# Rendered HTML structure.
# ---------------------------------------------------------------------------


def test_rendered_html_carries_each_scenario_derivation() -> None:
    html = render_diligence_html(_topics())
    assert "Trimble-anchored" in html
    assert "<th>Derivation</th>" in html
    assert "<th>Exit EV</th>" in html


def test_terms_gaps_render_a_blocking_banner() -> None:
    html = render_diligence_html(_topics(), terms_gaps=["gross_carry_pct", "allocation_usd"])
    assert "TERMS PARSE INCOMPLETE" in html
    assert "gross_carry_pct" in html


def test_no_gaps_means_no_banner() -> None:
    html = render_diligence_html(_topics())
    assert "TERMS PARSE INCOMPLETE" not in html


# ---------------------------------------------------------------------------
# Prompt assembly: the research blocks must actually reach the synthesis
# prompt (adversarial-review gap — _format_prompt had zero coverage).
# ---------------------------------------------------------------------------


def test_format_prompt_carries_research_blocks_and_terms_warning(tmp_path: Path) -> None:

    from angel_memos.diligence import _format_prompt
    from angel_memos.materials import FileEntry, Materials
    from angel_memos.models import AngelListMetadata, Stage
    from angel_memos.research import (
        CompanyEvent,
        ComparableDeals,
        CompetitorComp,
        RecentEvents,
    )

    al_pdf = tmp_path / "angellist - Acme.pdf"
    al_pdf.write_bytes(b"%PDF")
    materials = Materials(
        folder=tmp_path,
        angellist=FileEntry(path=al_pdf, kind="angellist"),
        deck=None,
        notes=[],
        other=[],
    )
    angellist_meta = AngelListMetadata.model_validate(
        {
            "company": "Acme",
            "round_label": "Series C+",
            "stage": Stage.SERIES_C.value,
            "instrument": "Preferred",
            "estimated_round_size_usd": 0.0,
            "share_class": "Preferred",
            "pre_money_usd": 1_000_000_000.0,
            "allocation_usd": 0.0,
            "estimated_expenses_pct": 0.0,
            "leads_investment_usd": 0.0,
            "gross_carry_pct": 0.0,
            "min_investment_usd": 0.0,
            "markets": ["Robotics"],
            "founders": ["Jane Doe"],
        }
    )
    comps = ComparableDeals(
        category="robotics",
        comps=[
            CompetitorComp(
                company_name="Symbotic",
                category_fit="public logistics automation",
                stage="Public",
                valuation_multiple=10.3,
                multiple_basis="P/S TTM",
                multiple_as_of="2026-07-31",
                sources=["stockanalysis.com"],
            ),
            CompetitorComp(
                company_name="Pickle",
                category_fit="truck unloading",
                stage="Series B",
                sources=["therobotreport.com"],
            ),
        ],
        summary="entry rich vs public comp",
    )
    events = RecentEvents(
        company="Acme",
        as_of="2026-07-31",
        events=[
            CompanyEvent(
                date="2026-01",
                headline="Customer CEO: 'pilot stage, not ready for prime time'",
                why_material="Contradicts production-scale framing.",
                source="yahoo.com",
                source_type="secondary",
            )
        ],
        searches_run=["Acme news 2026"],
    )

    prompt = _format_prompt(
        [Path("al.png")], [], materials, angellist_meta, None, [], comps, events
    )

    assert "RECENT EVENTS" in prompt
    assert "not ready for prime time" in prompt
    assert "10.3x P/S TTM as of 2026-07-31" in prompt
    assert "TERMS PARSE INCOMPLETE" in prompt  # fee fields are all 0.0 above
