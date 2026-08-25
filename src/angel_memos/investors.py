"""Persistent cross-deal investor database.

Grades syndicate leads and co-investors once, then reuses the grade across
deals — the compounding asset a per-deal cache can't build. Each record is
web-researched by Claude (track record, notable investments) and assigned
a grade the scorecard's `co_investors` factor consumes.

Storage: sqlite at `~/.angel-memos/investors.db` — deliberately OUTSIDE
the Google Drive tree. Drive-for-Desktop sync and sqlite locking corrupt
each other; the human-readable view is exported to the Drive root as
`investors.md` instead.

Staleness: records older than `STALE_AFTER_DAYS` get re-researched on next
lookup. `deals_seen` counts how many of the user's deals the investor has
appeared in (a proxy for how often you co-invest alongside them).
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from collections.abc import Callable
from datetime import date, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from angel_memos.claude import LlmCallError
from angel_memos.config import Config
from angel_memos.models import AngelListMetadata

DATA_DIR_ENV_VAR = "ANGEL_MEMOS_DATA_DIR"
DB_FILENAME = "investors.db"
EXPORT_FILENAME = "investors.md"
STALE_AFTER_DAYS = 180

logger = logging.getLogger(__name__)

_AL_CACHE_FILENAME = ".angellist_cache.json"


class InvestorGrade(StrEnum):
    """A = top-decile, B = solid institutional, C = active but
    undifferentiated, D = unknown / unverifiable / negative signals."""

    A = "A"
    B = "B"
    C = "C"
    D = "D"


class InvestorResearch(BaseModel):
    """Web-researched grading of one investor (what the LLM returns)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    investor_type: Literal["syndicate_lead", "vc_fund", "angel", "unknown"]
    grade: InvestorGrade
    grade_justification: str = Field(min_length=1)
    notable_investments: list[str] = []
    track_record_summary: str = Field(min_length=1)
    sources: list[str] = Field(min_length=1)


class InvestorRecord(BaseModel):
    """One stored investor: research payload + cross-deal bookkeeping."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(min_length=1)  # normalized name; primary key
    display_name: str = Field(min_length=1)
    research: InvestorResearch
    deals_seen: int = Field(ge=1)
    first_seen: date
    last_refreshed: date


type ResearchFn = Callable[[str, str], InvestorResearch]

_PUNCTUATION_EDGES = re.compile(r"^[\s\.,;:!\-]+|[\s\.,;:!\-]+$")
_WHITESPACE = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    """Canonical dict/DB key for an investor name: lowercase, collapsed
    whitespace, edge punctuation stripped."""
    return _WHITESPACE.sub(" ", _PUNCTUATION_EDGES.sub("", name)).lower().strip()


# ---------------------------------------------------------------------------
# Storage.
# ---------------------------------------------------------------------------


def data_dir() -> Path:
    """Local (non-Drive) data directory; override via ANGEL_MEMOS_DATA_DIR."""
    override = os.environ.get(DATA_DIR_ENV_VAR)
    if override:
        return Path(override)
    return Path.home() / ".angel-memos"


def db_path() -> Path:
    return data_dir() / DB_FILENAME


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open (creating if needed) the investor DB and ensure the schema."""
    target = path if path is not None else db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS investors (key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
    )
    conn.commit()
    return conn


def get_record(conn: sqlite3.Connection, name: str) -> InvestorRecord | None:
    row = conn.execute(
        "SELECT payload FROM investors WHERE key = ?", (normalize_name(name),)
    ).fetchone()
    if row is None:
        return None
    payload: str = row[0]
    return InvestorRecord.model_validate_json(payload)


def upsert_record(conn: sqlite3.Connection, record: InvestorRecord) -> None:
    conn.execute(
        "INSERT INTO investors (key, payload) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET payload = excluded.payload",
        (record.key, record.model_dump_json()),
    )
    conn.commit()


def all_records(conn: sqlite3.Connection) -> list[InvestorRecord]:
    """All records, best grades first, alphabetical within a grade."""
    rows = conn.execute("SELECT payload FROM investors").fetchall()
    records = [InvestorRecord.model_validate_json(row[0]) for row in rows]
    return sorted(records, key=lambda r: (r.research.grade.value, r.display_name.lower()))


# ---------------------------------------------------------------------------
# Grading via web research.
# ---------------------------------------------------------------------------

_GRADE_SYSTEM_PROMPT = """You are grading ONE startup investor (fund,
syndicate lead, or angel) for a personal cross-deal investor database.

REQUIRED searches (run all — do not skip):
  1. WebSearch: "<investor name> venture fund portfolio"
  2. WebSearch: "<investor name> AngelList syndicate"
  3. WebSearch: "<investor name> notable investments unicorn exit"
  4. WebSearch: "<investor name> fund size lead investor"

Grade rubric:
  A = top-decile signal: brand fund (Sequoia/a16z/Lux/Founders Fund
      tier) OR a lead with 2+ verifiable unicorn-or-exit picks at
      early stage
  B = solid institutional fund or syndicate lead with at least one
      verifiable strong outcome and consistent early-stage activity
  C = active investor with no differentiated, verifiable track record
  D = unknown, unverifiable, or negative signals (lawsuits, blowups,
      spray-and-pray with no wins)

BANNED outputs:
  - Fabricated portfolio companies or fund sizes. If unverifiable,
    grade D and say what you searched.
  - "Could not determine" as a justification — name the searches that
    came back empty instead.

`sources`: concrete URLs or short descriptions of what each search
returned. `notable_investments`: only companies you actually verified."""


def build_grade_prompt(name: str, context: str) -> str:
    """User-prompt body for one investor-grading call."""
    lines = [f"Investor to grade: {name}"]
    if context:
        lines.append(f"Context: {context}")
    lines.append(
        "\nRun the WebSearches specified in the system prompt. Return JSON "
        "matching the InvestorResearch schema. Grade honestly — D for "
        "unverifiable is more useful than a charitable C."
    )
    return "\n".join(lines)


def grade_investor(name: str, context: str) -> InvestorResearch:
    """Web-research one investor and return a graded profile.
    ~$0.05 / 30s per call; results persist in the DB for 180 days."""
    from angel_memos.claude import Purpose, extract_structured

    return extract_structured(
        build_grade_prompt(name, context),
        InvestorResearch,
        purpose=Purpose.INVESTOR_GRADE,
        system_prompt=_GRADE_SYSTEM_PROMPT,
    )


def _unverified_research(name: str) -> InvestorResearch:
    """Explicit degraded result when governed web research is unavailable.

    Grade D already owns unknown/unverifiable evidence in the persisted rubric.
    The deliberately stale timestamp applied by ``lookup_or_grade`` makes the
    next deal retry the research instead of trusting this operational fallback
    for the normal 180-day freshness window.
    """
    return InvestorResearch(
        name=name.strip(),
        investor_type="unknown",
        grade=InvestorGrade.D,
        grade_justification=(
            "Automated web research was unavailable; assigned Grade D per the "
            "unknown/unverifiable rubric rather than guessing."
        ),
        notable_investments=[],
        track_record_summary="Unverified; no attributable track record was established.",
        sources=["Automated web research unavailable; retry required."],
    )


def lookup_or_grade(
    conn: sqlite3.Connection,
    names: list[str],
    *,
    context: str = "",
    research_fn: ResearchFn = grade_investor,
    today: date | None = None,
) -> list[InvestorRecord]:
    """Resolve each name to a graded record: reuse fresh records (bumping
    `deals_seen`), re-research stale ones, research unknown ones. Names
    deduplicate via normalization; dedup within one call does NOT bump
    `deals_seen` (it's one deal)."""
    resolved_today = today or date.today()
    records: list[InvestorRecord] = []
    seen_keys: set[str] = set()
    for name in names:
        key = normalize_name(name)
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        existing = get_record(conn, name)
        if existing is not None:
            is_stale = (resolved_today - existing.last_refreshed).days > STALE_AFTER_DAYS
            research = existing.research
            last_refreshed = existing.last_refreshed
            if is_stale:
                try:
                    research = research_fn(name, context)
                    last_refreshed = resolved_today
                except LlmCallError:
                    logger.warning(
                        "investor research unavailable for %s; retaining stale grade",
                        name,
                    )
            record = InvestorRecord(
                key=key,
                display_name=existing.display_name,
                research=research,
                deals_seen=existing.deals_seen + 1,
                first_seen=existing.first_seen,
                last_refreshed=last_refreshed,
            )
        else:
            last_refreshed = resolved_today
            try:
                research = research_fn(name, context)
            except LlmCallError:
                logger.warning(
                    "investor research unavailable for %s; recording retryable Grade D",
                    name,
                )
                research = _unverified_research(name)
                last_refreshed = resolved_today - timedelta(days=STALE_AFTER_DAYS + 1)
            record = InvestorRecord(
                key=key,
                display_name=name.strip(),
                research=research,
                deals_seen=1,
                first_seen=resolved_today,
                last_refreshed=last_refreshed,
            )
        upsert_record(conn, record)
        records.append(record)
    return records


# ---------------------------------------------------------------------------
# Backfill from existing company folders.
# ---------------------------------------------------------------------------


def collect_known_investors(cfg: Config) -> dict[str, str]:
    """Scan Evaluation/ and Portfolio/ company folders for cached AngelList
    metadata and collect co-investor names: normalized key -> display name.
    Folders without a cache are skipped (no Claude calls here)."""
    found: dict[str, str] = {}
    for root in (cfg.evaluation_root, cfg.portfolio_root):
        if not root.is_dir():
            continue
        for folder in sorted(root.iterdir()):
            cache = folder / _AL_CACHE_FILENAME
            if not folder.is_dir() or not cache.is_file():
                continue
            metadata = AngelListMetadata.model_validate_json(cache.read_text(encoding="utf-8"))
            for name in metadata.co_investors:
                key = normalize_name(name)
                if key and key not in found:
                    found[key] = name.strip()
    return found


def backfill(
    conn: sqlite3.Connection,
    cfg: Config,
    *,
    research_fn: ResearchFn = grade_investor,
    today: date | None = None,
) -> list[InvestorRecord]:
    """Seed the DB from every co-investor named in existing deal caches."""
    known = collect_known_investors(cfg)
    return lookup_or_grade(
        conn,
        list(known.values()),
        context="Backfill from past deal folders.",
        research_fn=research_fn,
        today=today,
    )


# ---------------------------------------------------------------------------
# Markdown export — the human-readable Drive-side view.
# ---------------------------------------------------------------------------


def render_investors_markdown(records: list[InvestorRecord]) -> str:
    """Render the DB grouped by grade, best first."""
    if not records:
        return "# Investor Database\n\n(no investors graded yet)\n"
    lines: list[str] = ["# Investor Database", ""]
    by_grade: dict[InvestorGrade, list[InvestorRecord]] = {}
    for record in records:
        by_grade.setdefault(record.research.grade, []).append(record)
    for grade in InvestorGrade:
        members = by_grade.get(grade)
        if not members:
            continue
        lines.append(f"## Grade {grade.value}")
        lines.append("")
        for r in sorted(members, key=lambda m: m.display_name.lower()):
            notable = (
                f" Notable: {', '.join(r.research.notable_investments)}."
                if r.research.notable_investments
                else ""
            )
            lines.append(
                f"- **{r.display_name}** ({r.research.investor_type}, seen in "
                f"{r.deals_seen} deal{'s' if r.deals_seen != 1 else ''}): "
                f"{r.research.track_record_summary}{notable} "
                f"_(refreshed {r.last_refreshed.isoformat()})_"
            )
        lines.append("")
    return "\n".join(lines)


def export_markdown(conn: sqlite3.Connection, cfg: Config) -> Path:
    """Write the readable view next to the Evaluation/Portfolio roots."""
    out_path = cfg.evaluation_root.parent / EXPORT_FILENAME
    out_path.write_text(render_investors_markdown(all_records(conn)), encoding="utf-8")
    return out_path
