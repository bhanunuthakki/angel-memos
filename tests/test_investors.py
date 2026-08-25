"""Investor DB: name normalization, sqlite persistence, staleness-driven
refresh, backfill from company-folder caches, and markdown export. The
web-research grading call is injected as a plain callable."""

from datetime import date
from pathlib import Path

from angel_memos.claude import LlmCallError
from angel_memos.config import Config
from angel_memos.investors import (
    STALE_AFTER_DAYS,
    InvestorGrade,
    InvestorRecord,
    InvestorResearch,
    all_records,
    backfill,
    build_grade_prompt,
    collect_known_investors,
    connect,
    get_record,
    lookup_or_grade,
    normalize_name,
    render_investors_markdown,
    upsert_record,
)
from angel_memos.models import AngelListMetadata, Stage


def _research(
    name: str = "Lux Capital", grade: InvestorGrade = InvestorGrade.A
) -> InvestorResearch:
    return InvestorResearch(
        name=name,
        investor_type="vc_fund",
        grade=grade,
        grade_justification="Multiple deep-tech unicorns led.",
        notable_investments=["Anduril", "Applied Intuition"],
        track_record_summary="Top-decile deep-tech fund.",
        sources=["Crunchbase profile", "lux.vc portfolio page"],
    )


def _record(
    name: str = "Lux Capital",
    grade: InvestorGrade = InvestorGrade.A,
    last_refreshed: date | None = None,
) -> InvestorRecord:
    return InvestorRecord(
        key=normalize_name(name),
        display_name=name,
        research=_research(name, grade),
        deals_seen=1,
        first_seen=date(2026, 1, 10),
        last_refreshed=last_refreshed or date(2026, 7, 1),
    )


def _al_metadata(company: str, co_investors: list[str]) -> AngelListMetadata:
    return AngelListMetadata(
        company=company,
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
        markets=["AI"],
        founders=["Jane Doe"],
        co_investors=co_investors,
    )


# ---------------------------------------------------------------------------
# Name normalization.
# ---------------------------------------------------------------------------


def test_normalize_name_case_and_whitespace() -> None:
    assert normalize_name("  Lux   Capital ") == normalize_name("lux capital")


def test_normalize_name_strips_trailing_punctuation() -> None:
    assert normalize_name("Lux Capital.") == normalize_name("Lux Capital")


def test_normalize_name_distinct_names_stay_distinct() -> None:
    assert normalize_name("Lux Capital") != normalize_name("Lux Health")


# ---------------------------------------------------------------------------
# Persistence.
# ---------------------------------------------------------------------------


def test_upsert_and_get_roundtrip(tmp_path: Path) -> None:
    conn = connect(tmp_path / "investors.db")
    record = _record()
    upsert_record(conn, record)
    loaded = get_record(conn, "lux capital")
    assert loaded == record


def test_get_missing_returns_none(tmp_path: Path) -> None:
    conn = connect(tmp_path / "investors.db")
    assert get_record(conn, "nobody") is None


def test_upsert_overwrites_existing(tmp_path: Path) -> None:
    conn = connect(tmp_path / "investors.db")
    upsert_record(conn, _record(grade=InvestorGrade.B))
    upsert_record(conn, _record(grade=InvestorGrade.A))
    loaded = get_record(conn, "Lux Capital")
    assert loaded is not None
    assert loaded.research.grade is InvestorGrade.A


def test_all_records_sorted_by_grade_then_name(tmp_path: Path) -> None:
    conn = connect(tmp_path / "investors.db")
    upsert_record(conn, _record("Zed Fund", InvestorGrade.A))
    upsert_record(conn, _record("Acme Angels", InvestorGrade.C))
    upsert_record(conn, _record("Beta Capital", InvestorGrade.A))
    names = [r.display_name for r in all_records(conn)]
    assert names == ["Beta Capital", "Zed Fund", "Acme Angels"]


# ---------------------------------------------------------------------------
# Lookup-or-grade: staleness and dedup.
# ---------------------------------------------------------------------------


def test_lookup_grades_unknown_investor(tmp_path: Path) -> None:
    conn = connect(tmp_path / "investors.db")
    calls: list[str] = []

    def fake_research(name: str, context: str) -> InvestorResearch:
        calls.append(name)
        return _research(name)

    records = lookup_or_grade(
        conn, ["Lux Capital"], research_fn=fake_research, today=date(2026, 7, 11)
    )
    assert calls == ["Lux Capital"]
    assert len(records) == 1
    assert records[0].first_seen == date(2026, 7, 11)
    assert get_record(conn, "Lux Capital") is not None


def test_lookup_skips_research_when_fresh(tmp_path: Path) -> None:
    conn = connect(tmp_path / "investors.db")
    upsert_record(conn, _record(last_refreshed=date(2026, 7, 1)))
    calls: list[str] = []

    def fake_research(name: str, context: str) -> InvestorResearch:
        calls.append(name)
        return _research(name)

    lookup_or_grade(conn, ["Lux Capital"], research_fn=fake_research, today=date(2026, 7, 11))
    assert calls == []


def test_lookup_refreshes_stale_record_preserving_history(tmp_path: Path) -> None:
    conn = connect(tmp_path / "investors.db")
    stale_date = date(2025, 1, 1)
    upsert_record(conn, _record(grade=InvestorGrade.B, last_refreshed=stale_date))
    today = date(2026, 7, 11)
    assert (today - stale_date).days > STALE_AFTER_DAYS

    records = lookup_or_grade(
        conn,
        ["Lux Capital"],
        research_fn=lambda name, context: _research(name, InvestorGrade.A),
        today=today,
    )
    assert records[0].research.grade is InvestorGrade.A
    assert records[0].first_seen == date(2026, 1, 10)  # preserved
    assert records[0].last_refreshed == today


def test_lookup_increments_deals_seen_for_known_investor(tmp_path: Path) -> None:
    conn = connect(tmp_path / "investors.db")
    upsert_record(conn, _record(last_refreshed=date(2026, 7, 1)))
    records = lookup_or_grade(
        conn,
        ["Lux Capital"],
        research_fn=lambda name, context: _research(name),
        today=date(2026, 7, 11),
    )
    assert records[0].deals_seen == 2


def test_lookup_dedups_names_within_one_call(tmp_path: Path) -> None:
    conn = connect(tmp_path / "investors.db")
    calls: list[str] = []

    def fake_research(name: str, context: str) -> InvestorResearch:
        calls.append(name)
        return _research(name)

    records = lookup_or_grade(
        conn,
        ["Lux Capital", "lux capital", " LUX  CAPITAL "],
        research_fn=fake_research,
        today=date(2026, 7, 11),
    )
    assert len(calls) == 1
    assert len(records) == 1
    assert records[0].deals_seen == 1


def test_lookup_records_unverified_unknown_when_research_transport_fails(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "investors.db")
    today = date(2026, 7, 11)

    def failed_research(name: str, context: str) -> InvestorResearch:
        raise LlmCallError("all configured transports failed")

    records = lookup_or_grade(
        conn,
        ["K8"],
        research_fn=failed_research,
        today=today,
    )

    assert len(records) == 1
    assert records[0].research.grade is InvestorGrade.D
    assert records[0].research.investor_type == "unknown"
    assert "unavailable" in records[0].research.grade_justification.lower()
    assert (today - records[0].last_refreshed).days > STALE_AFTER_DAYS
    assert get_record(conn, "K8") == records[0]


def test_lookup_keeps_stale_grade_when_refresh_transport_fails(tmp_path: Path) -> None:
    conn = connect(tmp_path / "investors.db")
    stale = _record(grade=InvestorGrade.B, last_refreshed=date(2025, 1, 1))
    upsert_record(conn, stale)

    def failed_research(name: str, context: str) -> InvestorResearch:
        raise LlmCallError("all configured transports failed")

    records = lookup_or_grade(
        conn,
        ["Lux Capital"],
        research_fn=failed_research,
        today=date(2026, 7, 11),
    )

    assert records[0].research.grade is InvestorGrade.B
    assert records[0].last_refreshed == stale.last_refreshed


# ---------------------------------------------------------------------------
# Backfill from company folders.
# ---------------------------------------------------------------------------


def _write_al_cache(folder: Path, company: str, co_investors: list[str]) -> None:
    folder.mkdir(parents=True)
    (folder / ".angellist_cache.json").write_text(
        _al_metadata(company, co_investors).model_dump_json(), encoding="utf-8"
    )


def test_collect_known_investors_reads_al_caches(tmp_path: Path) -> None:
    cfg = Config(
        evaluation_root=tmp_path / "Evaluation",
        portfolio_root=tmp_path / "Portfolio",
    )
    _write_al_cache(cfg.evaluation_root / "Acme", "Acme", ["Lux Capital", "First Round"])
    _write_al_cache(cfg.portfolio_root / "Beta", "Beta", ["lux capital"])

    found = collect_known_investors(cfg)
    assert found[normalize_name("Lux Capital")] == "Lux Capital"
    assert normalize_name("First Round") in found


def test_collect_known_investors_missing_roots_is_empty(tmp_path: Path) -> None:
    cfg = Config(
        evaluation_root=tmp_path / "nope",
        portfolio_root=tmp_path / "also-nope",
    )
    assert collect_known_investors(cfg) == {}


def test_backfill_grades_every_collected_investor_once(tmp_path: Path) -> None:
    cfg = Config(
        evaluation_root=tmp_path / "Evaluation",
        portfolio_root=tmp_path / "Portfolio",
    )
    _write_al_cache(cfg.evaluation_root / "Acme", "Acme", ["Lux Capital", "First Round"])
    _write_al_cache(cfg.portfolio_root / "Beta", "Beta", ["lux capital"])
    conn = connect(tmp_path / "investors.db")
    calls: list[str] = []

    def fake_research(name: str, context: str) -> InvestorResearch:
        calls.append(name)
        return _research(name)

    records = backfill(conn, cfg, research_fn=fake_research, today=date(2026, 7, 11))
    assert sorted(calls) == ["First Round", "Lux Capital"]
    assert len(records) == 2


# ---------------------------------------------------------------------------
# Prompt + markdown export.
# ---------------------------------------------------------------------------


def test_build_grade_prompt_includes_name_and_context() -> None:
    prompt = build_grade_prompt("Lux Capital", "Co-investor on Acme seed round.")
    assert "Lux Capital" in prompt
    assert "Acme seed round" in prompt


def test_render_markdown_groups_by_grade() -> None:
    records = [
        _record("Zed Fund", InvestorGrade.A),
        _record("Acme Angels", InvestorGrade.C),
    ]
    out = render_investors_markdown(records)
    assert out.index("Zed Fund") < out.index("Acme Angels")
    assert "Grade A" in out
    assert "Grade C" in out
    assert "Anduril" in out  # notable investments surfaced


def test_render_markdown_empty_db() -> None:
    out = render_investors_markdown([])
    assert "no investors" in out.lower()
