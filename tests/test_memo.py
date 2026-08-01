"""Memo-phase guards that don't require Claude or Google Docs.

Focus: the pre-publish leak gate (`_guard_public_entry`), which must abort
before any external write when the anonymized public entry still contains a
deal-identifying string.
"""

from pathlib import Path

import pytest

from angel_memos.doc_entries import (
    BullCaseSection,
    KeyMetricsSection,
    MarketAndOpportunity,
    PublicDocEntry,
    TeamSection,
)
from angel_memos.masking import PublicMemoLeakError
from angel_memos.memo import (
    StaleEntryError,
    _check_entry_freshness,
    _guard_public_entry,
    _validate_long_memo,
    _write_entry_meta,
)
from angel_memos.models import Conviction, Decision, ValuationMethod, Verdict


def _decision() -> Decision:
    return Decision.model_validate(
        {
            "company": "Spot AI",
            "verdict": Verdict.BUY,
            "conviction": Conviction.HIGH,
            "check_usd": 10_000.0,
            "post_money_usd": 195_000_000.0,
            "valuation_method": ValuationMethod.CUSTOM,
            "current_base_metric_usd": None,
            "scenarios": None,
            "benchmarks": None,
            "top_reasons": ["a", "b", "c"],
            "top_risks": ["x", "y", "z"],
            "raw_reasoning": "r",
        }
    )


def _public_entry(**overrides: object) -> PublicDocEntry:
    base: dict[str, object] = {
        "category_descriptor": "Video Management Agents",
        "stage_label": "Series A",
        "date_label": "July 2026",
        "what_does_it_do": "Turns CCTV into searchable agents.",
        "why_is_it_important": "Physical-security teams are understaffed.",
        "market_and_opportunity": MarketAndOpportunity(
            job_to_be_done="Monitor sites without more headcount.",
            market_size="$XXB physical security market.",
            why_now="Vision models finally cheap enough.",
        ),
        "team": TeamSection(
            founder_market_fit="Founders shipped vision infra at scale.",
            superpower_or_execution_advantage="Distribution via installers.",
        ),
        "key_metrics": KeyMetricsSection(
            arr_or_contracted_revenue="mid-$XM CARR",
            retention="NDR >120%",
            efficiency_or_techno_economics="sub-$XXM post",
        ),
        "anti_thesis_paragraphs": [
            "Commoditization of vision models compresses the moat.",
            "Enterprise sales cycles could stall the burn math.",
        ],
        "bull_case": BullCaseSection(
            thesis="Workflow lock-in compounds with each install.",
            verdict="GO. Entry price is defensible on CARR multiple.",
        ),
    }
    base.update(overrides)
    return PublicDocEntry.model_validate(base)


def test_guard_passes_on_clean_entry(tmp_path: Path) -> None:
    # Empty folder -> AL metadata can't load; gate falls back to decision facts.
    _guard_public_entry(tmp_path, _decision(), _public_entry())


def test_guard_raises_on_company_name_leak(tmp_path: Path) -> None:
    entry = _public_entry(what_does_it_do="Spot AI turns CCTV into agents.")
    with pytest.raises(PublicMemoLeakError):
        _guard_public_entry(tmp_path, _decision(), entry)


def test_guard_raises_on_exact_figure_leak(tmp_path: Path) -> None:
    entry = _public_entry(
        market_and_opportunity=MarketAndOpportunity(
            job_to_be_done="x",
            market_size="Post-money was $195 million.",
            why_now="y",
        )
    )
    with pytest.raises(PublicMemoLeakError):
        _guard_public_entry(tmp_path, _decision(), entry)


def test_guard_raises_on_review_marker(tmp_path: Path) -> None:
    entry = _public_entry(why_is_it_important="[NEEDS BHANU REVIEW: confirm the moat] It matters.")
    with pytest.raises(PublicMemoLeakError):
        _guard_public_entry(tmp_path, _decision(), entry)


# ---------------------------------------------------------------------------
# Entry-freshness guard (#2): don't publish entries against a changed decision.
# ---------------------------------------------------------------------------

from datetime import date  # noqa: E402


def test_freshness_passes_when_decision_unchanged(tmp_path: Path) -> None:
    (tmp_path / "decision.md").write_text("company: X\nverdict: buy\n", encoding="utf-8")
    _write_entry_meta(tmp_path, date(2026, 7, 13))
    _check_entry_freshness(tmp_path)  # no raise


def test_freshness_raises_when_decision_edited(tmp_path: Path) -> None:
    decision = tmp_path / "decision.md"
    decision.write_text("company: X\nverdict: buy\n", encoding="utf-8")
    _write_entry_meta(tmp_path, date(2026, 7, 13))
    decision.write_text("company: X\nverdict: pass\n", encoding="utf-8")  # flip
    with pytest.raises(StaleEntryError):
        _check_entry_freshness(tmp_path)


def test_freshness_noop_when_meta_absent(tmp_path: Path) -> None:
    (tmp_path / "decision.md").write_text("company: X\n", encoding="utf-8")
    _check_entry_freshness(tmp_path)  # no meta -> can't prove staleness -> allow


# ---------------------------------------------------------------------------
# Long-memo validation (#8): never save a refusal/truncation as the memo.
# ---------------------------------------------------------------------------


def test_validate_long_memo_accepts_full_nine_section_memo() -> None:
    body = "# Acme — Investment Memo\n\n" + "\n\n".join(
        f"## {n}. Section {n}\n" + ("lorem ipsum " * 40) for n in range(1, 10)
    )
    assert _validate_long_memo(body) == []


def test_validate_long_memo_flags_refusal_text() -> None:
    problems = _validate_long_memo("I can't help with that request.")
    assert problems  # too short + missing sections


def test_validate_long_memo_flags_truncated_memo() -> None:
    body = "# Acme — Investment Memo\n\n" + "\n\n".join(
        f"## {n}. Section {n}\n" + ("lorem ipsum " * 40) for n in range(1, 6)
    )  # only sections 1-5
    problems = _validate_long_memo(body)
    assert any("missing sections" in p for p in problems)


# ---------------------------------------------------------------------------
# Long-memo validation: the MANDATORY tables are enforced against the actual
# Decision VALUES, not just headings (adversarial-review findings — a real
# memo shipped without the comparables table, and a fabricated table under
# the right heading would previously have passed).
# ---------------------------------------------------------------------------


def _arr_decision() -> Decision:
    return Decision.model_validate(
        {
            "company": "Acme",
            "verdict": "buy",
            "conviction": "medium",
            "check_usd": 5_000.0,
            "post_money_usd": 25_000_000.0,
            "valuation_method": "arr_multiple",
            "current_base_metric_usd": 400_000.0,
            "scenarios": [
                {
                    "name": "Zero",
                    "probability": 0.2,
                    "future_dilution": 0.6,
                    "cagr": 0.0,
                    "exit_multiple": 1.0,
                },
                {
                    "name": "Slow Grind",
                    "probability": 0.3,
                    "future_dilution": 0.5,
                    "cagr": 0.3,
                    "exit_multiple": 4.0,
                },
                {
                    "name": "Base",
                    "probability": 0.3,
                    "future_dilution": 0.4,
                    "cagr": 0.8,
                    "exit_multiple": 8.0,
                },
                {
                    "name": "Bull",
                    "probability": 0.15,
                    "future_dilution": 0.35,
                    "cagr": 1.2,
                    "exit_multiple": 10.0,
                },
                {
                    "name": "Generational",
                    "probability": 0.05,
                    "future_dilution": 0.3,
                    "cagr": 1.5,
                    "exit_multiple": 15.0,
                },
            ],
            "benchmarks": [
                {
                    "rank_label": "Top 1",
                    "comparable": "Symbotic",
                    "exit_valuation_usd": 26_010_000_000.0,
                    "terminal_arr_usd": 2_520_000_000.0,
                    "exit_multiple": 10.3,
                    "multiple_as_of": "2026-07-31",
                    "multiple_source": "stockanalysis.com",
                },
                {
                    "rank_label": "Top 5",
                    "comparable": "Berkshire Grey",
                    "exit_valuation_usd": 375_000_000.0,
                    "terminal_arr_usd": 66_000_000.0,
                    "exit_multiple": 5.7,
                },
            ],
            "top_reasons": ["a", "b", "c"],
            "top_risks": ["x", "y", "z"],
            "raw_reasoning": "r",
        }
    )


_COMPLIANT_S7 = (
    "Comparable multiples (as-of dated)\n\n\n"
    "| Comparable | Multiple | Basis | As-of date | Source |\n"
    "|---|---|---|---|---|\n"
    "| Symbotic | 10.3x | EV/ARR | 2026-07-31 | stockanalysis.com |\n"
    "| Berkshire Grey | 5.7x | EV/ARR | UNDATED - verify before relying | - |\n"
)

_COMPLIANT_S8 = (
    "| Scenario | Probability | Key Assumptions | Gross | Net MoM |\n"
    "|---|---|---|---|---|\n"
    "| Zero | 20% | fails | 0x | 0x |\n"
    "| Slow Grind | 30% | bundled away | 1.2x | 1.0x |\n"
    "| Base | 30% | durable niche | 4x | 3.2x |\n"
    "| Bull | 15% | category leader | 8x | 6.1x |\n"
    "| Generational | 5% | clearinghouse | 15x | 11x |\n"
)


def _memo_md(*, s7: str = _COMPLIANT_S7, s8: str = _COMPLIANT_S8) -> str:
    sections: list[str] = []
    for n in range(1, 10):
        body = "x" * 200
        if n == 7:
            body += "\n\n" + s7
        if n == 8:
            body += "\n\n" + s8
        sections.append(f"## {n}. Section\n{body}")
    return "# Acme - Investment Memo\n\n" + "\n\n".join(sections)


def test_memo_matching_decision_values_passes() -> None:
    assert _validate_long_memo(_memo_md(), decision=_arr_decision()) == []


def test_memo_missing_comparables_table_fails() -> None:
    problems = _validate_long_memo(_memo_md(s7=""), decision=_arr_decision())
    assert any("Comparable multiples" in p for p in problems)


def test_fabricated_table_under_the_right_heading_fails() -> None:
    """The residual gap from the adversarial pass: a heading with an empty or
    invented table must not satisfy the mandate."""
    fake = (
        "Comparable multiples (as-of dated)\n\n"
        "| Comparable | Multiple | Basis | As-of date | Source |\n"
        "|---|---|---|---|---|\n"
        "| MadeUpCo | 40x | EV/ARR | 2026-01-01 | vibes |\n"
    )
    problems = _validate_long_memo(_memo_md(s7=fake), decision=_arr_decision())
    assert any("Symbotic" in p for p in problems)
    assert any("Berkshire Grey" in p for p in problems)


def test_wrong_benchmark_multiple_fails() -> None:
    wrong = _COMPLIANT_S7.replace("10.3x", "40x")
    problems = _validate_long_memo(_memo_md(s7=wrong), decision=_arr_decision())
    assert any("Symbotic" in p and "10.3" in p for p in problems)


def test_missing_as_of_date_fails_without_undated_marker() -> None:
    no_marker = _COMPLIANT_S7.replace("UNDATED - verify before relying", "n/a")
    problems = _validate_long_memo(_memo_md(s7=no_marker), decision=_arr_decision())
    assert any("UNDATED" in p for p in problems)


def test_scenario_absent_or_wrong_probability_fails() -> None:
    missing_row = _COMPLIANT_S8.replace("| Generational | 5% | clearinghouse | 15x | 11x |\n", "")
    problems = _validate_long_memo(_memo_md(s8=missing_row), decision=_arr_decision())
    assert any("Generational" in p for p in problems)

    wrong_prob = _COMPLIANT_S8.replace("| Zero | 20% |", "| Zero | 45% |")
    problems = _validate_long_memo(_memo_md(s8=wrong_prob), decision=_arr_decision())
    assert any("Zero" in p and "probability" in p for p in problems)


def test_memo_missing_net_mom_column_fails() -> None:
    no_net = _COMPLIANT_S8.replace(" Net MoM |", " Net |").replace("| Net MoM", "| Net")
    problems = _validate_long_memo(_memo_md(s8=no_net), decision=_arr_decision())
    assert any("Net MoM" in p for p in problems)


def test_pass_decision_without_tables_still_validates() -> None:
    """A pass/custom decision has no benchmarks or scenarios; the memo owes
    no tables and probability checks must not fire."""
    assert _validate_long_memo(_memo_md(s7="", s8=""), decision=_decision()) == []


def test_decisionless_validation_keeps_structural_checks_only() -> None:
    assert _validate_long_memo(_memo_md(s7="", s8="")) == []
    assert _validate_long_memo("too short") != []


def test_prose_mentions_without_table_rows_fail() -> None:
    """Adversarial re-grade finding: the right names and numbers scattered in
    section PROSE (no table rows) must not satisfy the mandate."""
    s7 = (
        "Comparable multiples (as-of dated) are worth noting: Symbotic sits "
        "around 10.3 times revenue as of 2026-07-31, and Berkshire Grey at "
        "5.7 times, undated, though we do not build a formal table here."
    )
    s8 = (
        "Net MoM commentary: in the Zero scenario (weight 20%) the company "
        "fails; Slow Grind (30%), Base (30%), Bull (15%), Generational (5%)."
    )
    problems = _validate_long_memo(_memo_md(s7=s7, s8=s8), decision=_arr_decision())
    assert any("Symbotic" in p and "table row" in p for p in problems)
    assert any("Zero" in p and "table row" in p for p in problems)


def test_decimal_percent_formatting_is_accepted() -> None:
    """Adversarial re-grade finding: "20.0%" style column alignment is a
    legitimate rendering and must not be a production-blocking false reject."""
    s8 = (
        "| Scenario | Probability | Key Assumptions | Gross | Net MoM |\n"
        "|---|---|---|---|---|\n"
        "| Zero | 20.0% | fails | 0x | 0x |\n"
        "| Slow Grind | 30.0% | bundled | 1.2x | 1.0x |\n"
        "| Base | 30.0% | durable | 4x | 3.2x |\n"
        "| Bull | 15.0% | leader | 8x | 6.1x |\n"
        "| Generational | 5.0% | clearinghouse | 15x | 11x |\n"
    )
    assert _validate_long_memo(_memo_md(s8=s8), decision=_arr_decision()) == []


def test_trailing_decimal_multiple_is_accepted() -> None:
    """ "10.30x" parses to the same float as 10.3 and must match."""
    s7 = _COMPLIANT_S7.replace("10.3x", "10.30x")
    assert _validate_long_memo(_memo_md(s7=s7), decision=_arr_decision()) == []


def test_gross_mom_cell_cannot_satisfy_probability() -> None:
    """A row whose percent is wrong must fail even when another cell's
    number ("0.2x" gross) coincidentally equals the decimal probability."""
    s8 = _COMPLIANT_S8.replace(
        "| Zero | 20% | fails | 0x | 0x |", "| Zero | 45% | fails | 0.2x | 0x |"
    )
    problems = _validate_long_memo(_memo_md(s8=s8), decision=_arr_decision())
    assert any("Zero" in p and "probability" in p for p in problems)


def test_decorative_pipes_in_prose_are_not_a_table() -> None:
    """Final adversarial probe: a sentence dressed with two pipes but no
    |---| separator anywhere in the section must not count as a table row."""
    s7 = "Note | Symbotic traded at 10.3x as of 2026-07-31 per stockanalysis.com | end"
    s8 = "Net MoM note | Zero 20%, Slow Grind 30%, Base 30%, Bull 15%, Generational 5% | end"
    problems = _validate_long_memo(_memo_md(s7=s7, s8=s8), decision=_arr_decision())
    assert any("Symbotic" in p and "no table row" in p for p in problems)
    assert any("Zero" in p and "no table row" in p for p in problems)
