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
# Long-memo structural validation: the MANDATORY tables are enforced, not
# just prompted (adversarial-review finding — a real memo shipped without
# the comparables table and nothing caught it).
# ---------------------------------------------------------------------------


def _memo_md(*, comparables: bool, net_mom: bool) -> str:
    sections: list[str] = []
    for n in range(1, 10):
        body = "x" * 200
        if n == 7 and comparables:
            body += "\n\nComparable multiples (as-of dated)\n| Comp | Multiple | As-of |\n"
        if n == 8:
            body += "\n| Scenario | Gross | Net MoM |\n" if net_mom else "\n| Scenario | Gross |\n"
        sections.append(f"## {n}. Section\n{body}")
    return "# Acme — Investment Memo\n\n" + "\n\n".join(sections)


def test_memo_with_mandated_tables_passes() -> None:
    md = _memo_md(comparables=True, net_mom=True)
    assert _validate_long_memo(md, has_benchmarks=True, has_scenarios=True) == []


def test_memo_missing_comparables_table_fails_when_benchmarks_exist() -> None:
    problems = _validate_long_memo(_memo_md(comparables=False, net_mom=True), has_benchmarks=True)
    assert any("Comparable multiples" in p for p in problems)


def test_memo_missing_comparables_ok_without_benchmarks() -> None:
    """A pass/custom decision has no benchmarks; the table is not demanded."""
    assert _validate_long_memo(_memo_md(comparables=False, net_mom=True)) == []


def test_memo_missing_net_mom_column_fails() -> None:
    problems = _validate_long_memo(
        _memo_md(comparables=True, net_mom=False), has_benchmarks=True, has_scenarios=True
    )
    assert any("Net MoM" in p for p in problems)
