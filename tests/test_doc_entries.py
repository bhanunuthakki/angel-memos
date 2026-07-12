"""Deterministic helpers in `doc_entries`: heading derivation, markdown
rendering, prompt structural shape.

Claude-driven generators (`generate_private_entry`, `generate_public_entry`)
are not unit-tested here — they require a live model call. They get
end-to-end coverage from the publish path on real decisions.
"""

from datetime import date
from pathlib import Path

import pytest

from angel_memos.doc_entries import (
    BullCaseSection,
    CompetitiveMoatSection,
    KeyMetricsSection,
    MarketAndOpportunity,
    PrivateDocEntry,
    PublicDocEntry,
    TeamSection,
    build_private_entry_prompt,
    build_public_entry_prompt,
    private_company_heading,
    private_second_section_label,
    public_entry_heading,
    render_public_entry_markdown,
)
from angel_memos.materials import FileEntry, Materials
from angel_memos.models import (
    AngelListMetadata,
    ArrBenchmark,
    ArrScenario,
    Conviction,
    Decision,
    DeckContent,
    Stage,
    ValuationMethod,
    Verdict,
)


def _buy_decision(**overrides: object) -> Decision:
    base: dict[str, object] = {
        "company": "Zeno Moto",
        "verdict": Verdict.BUY,
        "conviction": Conviction.HIGH,
        "check_usd": 5000.0,
        "post_money_usd": 64_000_000.0,
        "valuation_method": ValuationMethod.ARR_MULTIPLE,
        "current_base_metric_usd": 1_000_000.0,
        "scenarios": [
            ArrScenario(
                name="Bull", probability=0.3, future_dilution=0.4, cagr=2.0, exit_multiple=15.0
            ),
            ArrScenario(
                name="Base", probability=0.4, future_dilution=0.5, cagr=1.0, exit_multiple=8.0
            ),
            ArrScenario(
                name="Zero", probability=0.3, future_dilution=0.7, cagr=0.0, exit_multiple=0.5
            ),
        ],
        "benchmarks": [
            ArrBenchmark(
                rank_label="Top 5",
                comparable="Snowflake",
                exit_valuation_usd=60_000_000_000.0,
                terminal_arr_usd=3_000_000_000.0,
                exit_multiple=20.0,
            ),
        ],
        "top_reasons": [
            "East Africa expertise + Tesla charging leadership",
            "Energy network gross-margin positive at low scale",
            "Massive market with structural cost parity",
        ],
        "top_risks": [
            "Execution on station rollout",
            "Hard-currency financing pipeline",
            "Battery chemistry shift risk",
        ],
        "raw_reasoning": "Co-investors are Congruent, Lowercarbon. Founders are battle-tested.",
    }
    base.update(overrides)
    return Decision.model_validate(base)


def _pass_decision(**overrides: object) -> Decision:
    base: dict[str, object] = {
        "company": "Anode",
        "verdict": Verdict.PASS,
        "conviction": Conviction.MEDIUM,
        "check_usd": 0.0,
        "post_money_usd": 34_000_000.0,
        "valuation_method": ValuationMethod.CUSTOM,
        "current_base_metric_usd": None,
        "scenarios": None,
        "benchmarks": None,
        "top_reasons": [
            "Pretty Rich at $34M Pre",
            "Founding team were CEO and VP Product at Moxion",
            "Awful Glassdoor reviews on Moxion management",
        ],
        "top_risks": [
            "Co-invested by Eclipse adds legitimacy",
            "Same use case as Moxion, replacing diesel generators",
            "Real market need for mobile power",
        ],
        "raw_reasoning": "Passing on management quality concerns.",
    }
    base.update(overrides)
    return Decision.model_validate(base)


def _angellist(**overrides: object) -> AngelListMetadata:
    base: dict[str, object] = {
        "company": "Zeno Motorcycles",
        "round_label": "Series A",
        "stage": Stage.SERIES_A,
        "instrument": "Equity",
        "estimated_round_size_usd": 19_000_000.0,
        "share_class": "Series A Preferred",
        "pre_money_usd": 45_000_000.0,
        "allocation_usd": 500_000.0,
        "estimated_expenses_pct": 0.005,
        "leads_investment_usd": 10_000_000.0,
        "gross_carry_pct": 0.20,
        "min_investment_usd": 5_000.0,
        "markets": ["Climate", "Transportation"],
        "founders": ["Michael Spencer", "Sam Heizer"],
        "co_investors": ["Congruent", "Lowercarbon"],
    }
    base.update(overrides)
    return AngelListMetadata.model_validate(base)


# ---------------------------------------------------------------------------
# Private heading + label derivation.
# ---------------------------------------------------------------------------


def test_private_heading_no_prefix_for_buy() -> None:
    assert private_company_heading(_buy_decision()) == "Zeno Moto"


def test_private_heading_no_prefix_for_strong_buy() -> None:
    d = _buy_decision(verdict=Verdict.STRONG_BUY)
    assert private_company_heading(d) == "Zeno Moto"


def test_private_heading_passed_prefix_for_pass() -> None:
    assert private_company_heading(_pass_decision()) == "[Passed] Anode"


def test_private_heading_passed_prefix_for_hold() -> None:
    d = _pass_decision(verdict=Verdict.HOLD)
    assert private_company_heading(d) == "[Passed] Anode"


def test_private_second_section_is_risks_for_buy() -> None:
    assert private_second_section_label(_buy_decision()) == "Risks"


def test_private_second_section_is_why_passing_for_pass() -> None:
    assert private_second_section_label(_pass_decision()) == "Why Passing?"


# ---------------------------------------------------------------------------
# Public-entry heading + markdown render.
# ---------------------------------------------------------------------------


def _public_entry(**overrides: object) -> PublicDocEntry:
    base: dict[str, object] = {
        "category_descriptor": "Emerging Market EV & Energy Infrastructure",
        "stage_label": "Series A",
        "date_label": "May 2026",
        "what_does_it_do": "Builds battery-swap stations for ride-hail drivers.",
        "why_is_it_important": "Decouples mobility cost from fuel imports.",
        "market_and_opportunity": MarketAndOpportunity(
            job_to_be_done="Move passengers cheaply.",
            market_size="$50B+ regional ride-hail spend.",
            why_now="Subsidy removal forced parity calc.",
        ),
        "team": TeamSection(
            founder_market_fit="CEO ran Tesla charging in region.",
            superpower_or_execution_advantage="Operating reps in three markets.",
        ),
        "key_metrics": KeyMetricsSection(
            arr_or_contracted_revenue=">$100M lifetime energy revenue secured",
            retention="Driver retention >85% at 12 months",
            efficiency_or_techno_economics="Cost-per-km at parity with petrol",
        ),
        "anti_thesis_paragraphs": [
            "Cap intensity could explode if station unit cost regresses.",
            "Driver economics depend on subsidy regime staying intact.",
        ],
        "bull_case": BullCaseSection(
            thesis="Network compounds as drivers cluster around hubs.",
            verdict="GO. Sub-$XXM post for a company with operating reps.",
        ),
    }
    base.update(overrides)
    return PublicDocEntry.model_validate(base)


def test_public_entry_heading_format() -> None:
    entry = _public_entry(category_descriptor="Synthetic Feed Additives", stage_label="Seed")
    assert public_entry_heading(entry) == "Synthetic Feed Additives — Seed Deal Memo"


def test_render_public_markdown_contains_required_sections() -> None:
    """Render must include each top-level section label."""
    md = render_public_entry_markdown(_public_entry())
    for label in (
        "What does it do?",
        "Why is it important?",
        "Market & Opportunity",
        "Team",
        "Key Metrics (Anonymized Ranges)",
        "Anti-Thesis",
        "Bull Case",
    ):
        assert label in md


def test_render_public_markdown_includes_competitive_moat_when_present() -> None:
    entry = _public_entry(
        competitive_moat=CompetitiveMoatSection(
            structural_advantage="Workflow lock-in",
            execution_velocity="Two ship-events per week",
        )
    )
    md = render_public_entry_markdown(entry)
    assert "Competitive Moat & Company Superpower" in md
    assert "Workflow lock-in" in md


def test_render_public_markdown_omits_competitive_moat_when_absent() -> None:
    md = render_public_entry_markdown(_public_entry(competitive_moat=None))
    assert "Competitive Moat" not in md


def test_render_public_markdown_starts_with_heading() -> None:
    md = render_public_entry_markdown(_public_entry())
    assert md.startswith("#### ")


# ---------------------------------------------------------------------------
# Prompt-builder structural shape.
# ---------------------------------------------------------------------------


def test_private_prompt_includes_passed_prefix_for_pass() -> None:
    prompt = build_private_entry_prompt(_pass_decision(), _angellist(), None)
    assert "[Passed] Anode" in prompt


def test_private_prompt_omits_passed_prefix_for_buy() -> None:
    prompt = build_private_entry_prompt(_buy_decision(), _angellist(), None)
    assert "[Passed]" not in prompt


def test_private_prompt_includes_check_for_buy() -> None:
    prompt = build_private_entry_prompt(_buy_decision(check_usd=7500.0), _angellist(), None)
    assert "$7,500" in prompt


def test_private_prompt_omits_check_for_pass() -> None:
    """Pass decisions have check_usd=0; rendering it pollutes Claude's input."""
    prompt = build_private_entry_prompt(_pass_decision(), _angellist(), None)
    assert "Check: $0" not in prompt


def test_private_prompt_includes_top_reasons_and_risks() -> None:
    decision = _buy_decision()
    prompt = build_private_entry_prompt(decision, _angellist(), None)
    for reason in decision.top_reasons:
        assert reason in prompt
    for risk in decision.top_risks:
        assert risk in prompt


def test_private_prompt_includes_co_investors() -> None:
    al = _angellist(co_investors=["Congruent", "Lowercarbon"])
    prompt = build_private_entry_prompt(_buy_decision(), al, None)
    assert "Congruent" in prompt
    assert "Lowercarbon" in prompt


def test_private_prompt_handles_empty_founders() -> None:
    al = _angellist(founders=[])
    prompt = build_private_entry_prompt(_buy_decision(), al, None)
    assert "(not surfaced)" in prompt


def test_private_prompt_includes_deck_when_provided() -> None:
    deck = DeckContent(
        product_description="Battery swap stations.",
        icp="Ride-hail drivers.",
        primary_use_case="Quick swap during shift.",
        pricing_model="",
        traction="3 stations live.",
        market_claims="",
        gtm_motion="Direct to drivers.",
        roadmap="",
        differentiation="Co-located with debt-financed batteries.",
    )
    prompt = build_private_entry_prompt(_buy_decision(), _angellist(), deck)
    assert "Battery swap stations" in prompt
    assert "PITCH-DECK SYNTHESIS" in prompt


def test_private_prompt_section_hint_matches_verdict() -> None:
    """The hint about what each section should contain depends on verdict."""
    buy_prompt = build_private_entry_prompt(_buy_decision(), _angellist(), None)
    pass_prompt = build_private_entry_prompt(_pass_decision(), _angellist(), None)
    assert "Risks" in buy_prompt
    assert "Why Passing?" in pass_prompt


def test_public_prompt_includes_anonymization_instructions() -> None:
    materials = Materials(folder=_dummy_folder(), angellist=_dummy_al_entry(), deck=None, notes=[])
    prompt = build_public_entry_prompt(
        _buy_decision(), _angellist(), None, materials, date(2026, 5, 15)
    )
    assert "category descriptor" in prompt.lower()
    assert "title only" in prompt.lower() or "titles only" in prompt.lower()


def test_public_prompt_includes_date_label() -> None:
    materials = Materials(folder=_dummy_folder(), angellist=_dummy_al_entry(), deck=None, notes=[])
    prompt = build_public_entry_prompt(
        _buy_decision(), _angellist(), None, materials, date(2026, 5, 15)
    )
    assert "May 2026" in prompt


def test_public_prompt_includes_scenarios_for_buy() -> None:
    materials = Materials(folder=_dummy_folder(), angellist=_dummy_al_entry(), deck=None, notes=[])
    prompt = build_public_entry_prompt(
        _buy_decision(), _angellist(), None, materials, date(2026, 5, 15)
    )
    # The scenarios field should appear in some form (JSON serialization
    # includes the field name).
    assert "scenarios" in prompt.lower() or "Bull" in prompt


def _dummy_folder() -> Path:
    """Any real directory works; the prompt builder only reads `.notes`."""
    import tempfile

    return Path(tempfile.gettempdir())


def _dummy_al_entry() -> FileEntry:
    """The materials object requires a non-None angellist FileEntry. We provide
    a minimal stub since the prompt builder only reads `.notes`, not the AL
    path."""
    return FileEntry(path=Path(__file__), kind="angellist")


# ---------------------------------------------------------------------------
# Pydantic-model field bounds (defensive sanity checks).
# ---------------------------------------------------------------------------


def test_private_entry_rejects_too_few_rationale_bullets() -> None:
    with pytest.raises(Exception):
        PrivateDocEntry(rationale_bullets=["only one"], second_section_bullets=["x"])


def test_private_entry_accepts_minimum_shape() -> None:
    entry = PrivateDocEntry(
        rationale_bullets=["bullet 1", "bullet 2"],
        second_section_bullets=["risk"],
    )
    assert len(entry.rationale_bullets) == 2


def test_public_entry_rejects_too_few_anti_thesis_paragraphs() -> None:
    with pytest.raises(Exception):
        _public_entry(anti_thesis_paragraphs=["only one"])


def test_public_entry_accepts_optional_moat() -> None:
    entry = _public_entry(competitive_moat=None)
    assert entry.competitive_moat is None
