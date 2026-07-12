"""Deterministic scoring logic: factor math, consensus/critique combination,
band mapping, report assembly, brief building, and markdown rendering.
LLM-judge calls are injected as plain callables so nothing here touches
Claude."""

import pytest
from pydantic import ValidationError

from angel_memos.models import AngelListMetadata, Stage
from angel_memos.scoring import (
    DEFAULT_WEIGHTS,
    Confidence,
    FactorName,
    FactorScore,
    JudgeSample,
    ScoreBand,
    ScoreReport,
    aggregate_total,
    apply_critique,
    band_for,
    build_deal_brief,
    build_report,
    build_summary,
    coinvestor_score,
    consensus,
    render_score_markdown,
    team_score,
    valuation_score,
)


def _factor(**overrides: object) -> FactorScore:
    base: dict[str, object] = {
        "name": FactorName.TEAM,
        "score": 70.0,
        "weight": 0.30,
        "confidence": Confidence.HIGH,
        "rationale": "Tier-A repeat founder.",
        "method": "deterministic",
    }
    base.update(overrides)
    return FactorScore.model_validate(base)


def _all_factors() -> list[FactorScore]:
    return [
        _factor(name=FactorName.TEAM, score=80.0, weight=DEFAULT_WEIGHTS[FactorName.TEAM]),
        _factor(
            name=FactorName.CO_INVESTORS,
            score=60.0,
            weight=DEFAULT_WEIGHTS[FactorName.CO_INVESTORS],
        ),
        _factor(name=FactorName.MARKET, score=50.0, weight=DEFAULT_WEIGHTS[FactorName.MARKET]),
        _factor(
            name=FactorName.TRACTION_TECH,
            score=40.0,
            weight=DEFAULT_WEIGHTS[FactorName.TRACTION_TECH],
        ),
        _factor(
            name=FactorName.TERMS_VALUATION,
            score=55.0,
            weight=DEFAULT_WEIGHTS[FactorName.TERMS_VALUATION],
            red_flags=["Post-money 2.1x comp median"],
        ),
    ]


# ---------------------------------------------------------------------------
# Weights and model bounds.
# ---------------------------------------------------------------------------


def test_default_weights_sum_to_one() -> None:
    assert abs(sum(DEFAULT_WEIGHTS.values()) - 1.0) < 1e-9


def test_default_weights_cover_all_factors() -> None:
    assert set(DEFAULT_WEIGHTS) == set(FactorName)


def test_factor_score_rejects_out_of_range() -> None:
    with pytest.raises(ValidationError):
        _factor(score=101.0)
    with pytest.raises(ValidationError):
        _factor(score=-1.0)


def test_judge_sample_requires_rationale() -> None:
    with pytest.raises(ValidationError):
        JudgeSample.model_validate({"score": 50.0, "rationale": ""})


# ---------------------------------------------------------------------------
# Deterministic factor math.
# ---------------------------------------------------------------------------


def test_team_score_single_s_tier_scores_high() -> None:
    score, confidence = team_score(["S"])
    assert score >= 90.0
    assert confidence is Confidence.MEDIUM  # single founder = medium


def test_team_score_best_founder_dominates() -> None:
    solo_d, _ = team_score(["D"])
    a_plus_d, _ = team_score(["A", "D"])
    assert a_plus_d > solo_d + 20  # one strong founder lifts the pair


def test_team_score_empty_is_low_confidence_and_weak() -> None:
    score, confidence = team_score([])
    assert score <= 35.0
    assert confidence is Confidence.LOW


def test_team_score_two_founders_high_confidence() -> None:
    _, confidence = team_score(["A", "B"])
    assert confidence is Confidence.HIGH


def test_coinvestor_score_orders_grades() -> None:
    a, _ = coinvestor_score(["A"])
    b, _ = coinvestor_score(["B"])
    d, _ = coinvestor_score(["D"])
    assert a > b > d


def test_coinvestor_score_empty_is_neutral_low_confidence() -> None:
    score, confidence = coinvestor_score([])
    assert 40.0 <= score <= 50.0
    assert confidence is Confidence.LOW


def test_valuation_score_cheap_vs_comps_scores_high() -> None:
    score, _, rationale = valuation_score(10_000_000, [25_000_000, 30_000_000])
    assert score >= 80.0
    assert "median" in rationale.lower()


def test_valuation_score_rich_vs_comps_scores_low() -> None:
    score, _, _ = valuation_score(90_000_000, [25_000_000, 30_000_000])
    assert score <= 30.0


def test_valuation_score_monotone_in_ratio() -> None:
    comps = [20_000_000.0]
    scores = [valuation_score(pm, comps)[0] for pm in (10e6, 18e6, 25e6, 45e6, 80e6)]
    assert scores == sorted(scores, reverse=True)


def test_valuation_score_no_comps_is_neutral_low_confidence() -> None:
    score, confidence, rationale = valuation_score(50_000_000, [])
    assert score == 50.0
    assert confidence is Confidence.LOW
    assert "no comparable" in rationale.lower()


# ---------------------------------------------------------------------------
# Consensus + critique.
# ---------------------------------------------------------------------------


def _sample(score: float, rationale: str = "r", red_flags: list[str] | None = None) -> JudgeSample:
    return JudgeSample(score=score, rationale=rationale, red_flags=red_flags or [])


def test_consensus_takes_median_score() -> None:
    result = consensus([_sample(40), _sample(60, "mid"), _sample(90)])
    assert result.score == 60.0
    assert result.rationale == "mid"  # rationale from sample closest to median


def test_consensus_even_count_averages_middle_pair() -> None:
    result = consensus([_sample(40), _sample(60)])
    assert result.score == 50.0


def test_consensus_tight_spread_is_high_confidence() -> None:
    result = consensus([_sample(58), _sample(60), _sample(62)])
    assert result.confidence is Confidence.HIGH
    assert result.spread == 4.0


def test_consensus_wide_spread_is_low_confidence() -> None:
    result = consensus([_sample(20), _sample(50), _sample(85)])
    assert result.confidence is Confidence.LOW


def test_consensus_unions_red_flags_preserving_order() -> None:
    result = consensus(
        [
            _sample(50, red_flags=["flag a", "flag b"]),
            _sample(55, red_flags=["flag b", "flag c"]),
        ]
    )
    assert result.red_flags == ["flag a", "flag b", "flag c"]


def test_consensus_rejects_empty() -> None:
    with pytest.raises(ValueError):
        consensus([])


def test_apply_critique_agreement_keeps_consensus() -> None:
    final, contested = apply_critique(60.0, 65.0)
    assert final == 60.0
    assert contested is False


def test_apply_critique_disagreement_meets_midway_and_flags() -> None:
    final, contested = apply_critique(70.0, 40.0)
    assert final == 55.0
    assert contested is True


# ---------------------------------------------------------------------------
# Aggregation, banding, report assembly.
# ---------------------------------------------------------------------------


def test_aggregate_total_is_weighted_sum() -> None:
    total = aggregate_total(_all_factors())
    expected = 80 * 0.30 + 60 * 0.15 + 50 * 0.15 + 40 * 0.20 + 55 * 0.20
    assert abs(total - expected) < 1e-9


def test_band_boundaries() -> None:
    assert band_for(70.0) is ScoreBand.STRONG_CANDIDATE
    assert band_for(69.9) is ScoreBand.CONSIDER
    assert band_for(55.0) is ScoreBand.CONSIDER
    assert band_for(54.9) is ScoreBand.BORDERLINE
    assert band_for(40.0) is ScoreBand.BORDERLINE
    assert band_for(39.9) is ScoreBand.PASS


def test_build_report_computes_total_band_and_flags() -> None:
    report = build_report("Acme", "quick", _all_factors(), summary="Solid team, rich price.")
    assert report.band is band_for(report.total)
    assert abs(report.total - aggregate_total(_all_factors())) < 1e-9
    assert "Post-money 2.1x comp median" in report.red_flags


def test_report_rejects_weights_not_summing_to_one() -> None:
    factors = _all_factors()
    factors[0] = _factor(name=FactorName.TEAM, weight=0.5)
    with pytest.raises(ValidationError):
        build_report("Acme", "quick", factors, summary="x")


def test_report_json_roundtrip() -> None:
    report = build_report("Acme", "deep", _all_factors(), summary="s")
    restored = ScoreReport.model_validate_json(report.model_dump_json())
    assert restored == report


# ---------------------------------------------------------------------------
# Markdown rendering.
# ---------------------------------------------------------------------------


def test_render_markdown_contains_total_band_and_factors() -> None:
    report = build_report("Acme", "quick", _all_factors(), summary="Solid team, rich price.")
    out = render_score_markdown(report)
    assert "Acme" in out
    assert f"{report.total:.0f}" in out
    for factor in report.factors:
        assert factor.name.value in out
    assert "Solid team, rich price." in out


def test_render_markdown_lists_red_flags() -> None:
    report = build_report("Acme", "quick", _all_factors(), summary="s")
    out = render_score_markdown(report)
    assert "Post-money 2.1x comp median" in out


def _al_metadata() -> AngelListMetadata:
    return AngelListMetadata(
        company="Acme",
        round_label="Seed",
        stage=Stage.SEED,
        instrument="SAFE",
        estimated_round_size_usd=3_000_000,
        share_class="Preferred",
        pre_money_usd=15_000_000,
        allocation_usd=200_000,
        estimated_expenses_pct=0.02,
        leads_investment_usd=1_000_000,
        gross_carry_pct=0.20,
        min_investment_usd=1_000,
        markets=["AI infrastructure"],
        founders=["Jane Doe"],
        co_investors=["Lux Capital"],
    )


# ---------------------------------------------------------------------------
# Deal brief + summary.
# ---------------------------------------------------------------------------


def test_build_deal_brief_includes_terms_and_blocks() -> None:
    brief = build_deal_brief(
        _al_metadata(),
        deck_text="Product: robots for warehouses.",
        founders_text="FOUNDER PROFILES: Jane Doe Tier A",
        comps_text="COMPARABLE DEALS: BetaCo $25M",
        investors_text="Lux Capital — Grade A",
        notes_text="Call note: strong demo.",
        research_memo_text="",
    )
    assert "Acme" in brief
    assert "$18,000,000" in brief  # post-money = pre + round
    assert "robots for warehouses" in brief
    assert "Tier A" in brief
    assert "BetaCo" in brief
    assert "Grade A" in brief
    assert "strong demo" in brief


def test_build_deal_brief_includes_research_memo_when_present() -> None:
    brief = build_deal_brief(
        _al_metadata(),
        deck_text="",
        founders_text="",
        comps_text="",
        investors_text="",
        notes_text="",
        research_memo_text="TRL is 4; capex heavy.",
    )
    assert "TRL is 4" in brief


def test_build_summary_names_strongest_and_weakest_factors() -> None:
    summary = build_summary(_all_factors())
    assert "team" in summary  # strongest (80)
    assert "traction_tech" in summary  # weakest (40)


def test_render_markdown_marks_contested_factors() -> None:
    factors = _all_factors()
    factors[2] = _factor(
        name=FactorName.MARKET,
        weight=DEFAULT_WEIGHTS[FactorName.MARKET],
        method="llm_judge",
        contested=True,
    )
    out = render_score_markdown(build_report("Acme", "quick", factors, summary="s"))
    assert "contested" in out.lower()
