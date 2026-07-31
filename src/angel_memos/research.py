"""Focused web-research passes for diligence.

Two focused Claude calls — each with a single job and a small structured
schema — that produce text the synthesis pass can ground its analysis in:

  1. `profile_founder` — one founder at a time. WebSearches LinkedIn, prior
     companies, exits, education. Returns a `FounderProfile` with a
     pedigree tier (S/A/B/C/D) and explicit sources.

  2. `find_comparable_deals` — ~3 funded companies in the same category +
     stage. WebSearches recent round sizes / valuations / ARR / co-
     investors. Returns `ComparableDeals` that anchors valuation discussion
     in real comps instead of "feels rich / feels fair" handwaves.

Why split into focused passes instead of pushing harder on the single
diligence call: empirically Claude skims when given 30+ image attachments +
a complex 9-section schema + WebSearch instructions. Small single-purpose
calls reliably do their one job. The synthesis pass then consumes the
pre-researched text as part of its input, the same way the diligence pass
already consumes parsed `DeckContent`.

Outputs are cached per-company so re-running diligence doesn't repeat the
~$0.05 / 30s per founder profile or ~$0.10 / 60s for the comp set.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from angel_memos.claude import Purpose, extract_structured
from angel_memos.models import Stage

PedigreeTier = Literal["S", "A", "B", "C", "D"]

_FOUNDER_PROFILE_SYSTEM_PROMPT = """You are researching ONE founder of an
early-stage startup. The investor reviewing this needs a sharp,
verifiable read on the founder's pedigree and likely execution capability.

REQUIRED searches (run all of them — do not skip):
  1. WebSearch: "<founder name> LinkedIn <current company name>"
  2. WebSearch: "<founder name> founder exit acquisition"
  3. WebSearch: "<founder name> CEO CTO previously"
  4. For each prior company you identify:
     - WebSearch: "<prior company> acquisition IPO outcome"
     - WebSearch: "<prior company> Glassdoor reviews" (sentiment only —
       don't transcribe; just note if reviews flag leadership concerns)

Pedigree rubric:
  S = repeat founder with prior exit > $100M
  A = repeat founder (any exit), OR former FAANG-AI / frontier-lab
      (OpenAI/Anthropic/DeepMind/Meta FAIR/Google Brain) / unicorn
      staff-or-above
  B = top-10 PhD with shipped research OR top-tier consulting/IB +
      operating role at a category-defining startup
  C = A-tier school OR A-tier prior employer, first-time founder
  D = unknown / unverifiable pedigree

BANNED outputs:
  - "Could not find / Unknown / TBD" — if WebSearch returns nothing
    useful, EXPLICITLY state what you searched, what came back, and what
    you concluded. Don't leave fields as placeholders.
  - "Cannot verify without LinkedIn access" — you have WebSearch. Use it.
  - Fabricated employer / school names. If unsure, leave the field empty.

Fill `web_research_summary` with 2-3 sentences synthesizing what you
found. Fill `sources` with concrete URLs or descriptions of what each
search returned ("LinkedIn page found at <url>", "Crunchbase profile",
"no results for <query>")."""


_COMPARABLE_DEALS_SYSTEM_PROMPT = """You are finding 2-3 funded companies
that are real comparables for a target startup — same category, similar
stage, similar business model.

REQUIRED searches:
  1. WebSearch: "<category keyword> <stage> funding 2024 2025 2026"
  2. WebSearch: "<category keyword> Series A Series B raised"
  3. For each candidate comp, WebSearch: "<comp name> Series funding
     valuation ARR"

A real comparable matches BOTH:
  - Category: same business model and customer (not "all climate
    startups" — actual same wedge / use case)
  - Stage: within ±1 stage of the target

BANNED outputs:
  - Picking household-name megacaps as "comps" for a Seed (e.g., naming
    Snowflake as a comp for a seed-stage data tool). Stage match matters.
  - Fabricated valuations or round sizes. If you can't verify a number
    via WebSearch, leave that field null and note "not disclosed" in
    `notes`.
  - Multiples from memory. When BOTH a valuation and a revenue figure
    were verified via WebSearch, compute `valuation_multiple`, name its
    `multiple_basis` (e.g. "EV / ARR"), and stamp `multiple_as_of` with
    today's date. Otherwise leave all three empty — an undated multiple
    is worse than none (observed off 2x within a single quarter).

OUTPUT SIZE DISCIPLINE: keep each comp's source list to 2-4 entries
(short descriptions, not full multi-line URL prose). The cumulative
response must fit within Opus's per-call output budget — terser is
better.

Fill `summary` with 1-2 sentences on what the comp set says about the
target's entry valuation (e.g., "Target's $50M Pre is rich vs. the median
$25M Pre in the comp set; only the Top-1 comp raised at higher.")."""


# ---------------------------------------------------------------------------
# Models.
# ---------------------------------------------------------------------------


class PriorEmployer(BaseModel):
    """One prior employer + outcome found via web research."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    company: str = Field(min_length=1)
    role: str = Field(min_length=1)
    outcome: str = ""  # e.g., "acquired by X for $Y", "IPO'd 2021", "shut down 2024"


class FounderProfile(BaseModel):
    """Web-researched profile of one founder.

    Concrete content over hedging. If WebSearch returned nothing useful,
    `web_research_summary` SHOULD say so explicitly — but `pedigree_tier`
    still gets assigned (D = unknown/unverifiable) rather than left blank.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    role: str = ""  # current role at the target company (CEO, CTO, etc.)
    prior_employers: list[PriorEmployer] = []
    education: list[str] = []
    notable_outcomes: list[str] = []  # "Exited Moxion at $XXM 2023", "IPO'd $YC", etc.
    pedigree_tier: PedigreeTier
    pedigree_justification: str = Field(min_length=1)
    web_research_summary: str = Field(min_length=1)
    linkedin_url: str = ""  # empty if not found
    sources: list[str] = Field(min_length=1)


class CompetitorComp(BaseModel):
    """One web-researched comparable company round. Source list capped so
    the cumulative response stays within Claude's per-call output budget
    when multiple comps appear in one ComparableDeals object."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    company_name: str = Field(min_length=1)
    category_fit: str = Field(min_length=1)  # 1-sentence why-comparable
    stage: str = Field(min_length=1)  # "Seed", "Series A", etc.
    last_round_usd: float | None = Field(default=None, ge=0.0)
    last_round_date: str = ""  # "Q3 2025" / "March 2024" / empty if unknown
    valuation_usd: float | None = Field(default=None, ge=0.0)
    arr_usd: float | None = Field(default=None, ge=0.0)
    # The comp's valuation multiple, dated. A multiple is only usable when it
    # carries its basis and fetch date — undated multiples drift stale in the
    # cache and have been observed off 2x within a quarter. None when the
    # inputs (valuation + a revenue figure) aren't publicly available.
    valuation_multiple: float | None = Field(default=None, ge=0.0)
    multiple_basis: str = ""  # e.g. "EV / ARR", "P/S TTM"; empty iff multiple is None
    multiple_as_of: str = ""  # date the figures were fetched, e.g. "2026-07-31"
    co_investors: list[str] = []
    notes: str = ""
    sources: list[str] = Field(min_length=1, max_length=4)


class ComparableDeals(BaseModel):
    """A small set of real comparables for a target startup.

    Capped at 3 comps (not 4-5) to keep the cumulative response size
    within Claude's output budget — empirically a 4-comp response with
    detailed sources gets truncated mid-URL on Opus."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: str = Field(min_length=1)
    comps: list[CompetitorComp] = Field(min_length=2, max_length=3)
    summary: str = Field(min_length=1)  # read on what the comps say about pricing


# ---------------------------------------------------------------------------
# Claude generators.
# ---------------------------------------------------------------------------


def profile_founder(founder_name: str, company_name: str) -> FounderProfile:
    """Web-research one founder and return a structured profile.

    Uses Claude with WebSearch enabled to find LinkedIn, prior companies,
    exits, education. ~$0.05 / 30s per call.
    """
    prompt = build_founder_profile_prompt(founder_name, company_name)
    return extract_structured(
        prompt,
        FounderProfile,
        purpose=Purpose.FOUNDER_PROFILE,
        system_prompt=_FOUNDER_PROFILE_SYSTEM_PROMPT,
    )


def find_comparable_deals(
    company_name: str,
    category_keywords: list[str],
    stage: Stage,
    product_summary: str,
) -> ComparableDeals:
    """Web-research 2-4 comparable funded companies in the same category
    and stage. ~$0.10 / 60s per call."""
    prompt = build_comparable_deals_prompt(company_name, category_keywords, stage, product_summary)
    return extract_structured(
        prompt,
        ComparableDeals,
        purpose=Purpose.COMPARABLE_DEALS,
        system_prompt=_COMPARABLE_DEALS_SYSTEM_PROMPT,
    )


# ---------------------------------------------------------------------------
# Prompt builders — pure functions, unit-testable.
# ---------------------------------------------------------------------------


def build_founder_profile_prompt(founder_name: str, company_name: str) -> str:
    """Construct the user-prompt body for one founder-profile call."""
    return (
        f"Research the founder: {founder_name}\n"
        f"Current company: {company_name}\n\n"
        "Run the WebSearches specified in the system prompt. Return JSON "
        "matching the FounderProfile schema. Every field must be filled — "
        "if a search returned nothing useful, note that EXPLICITLY in "
        "web_research_summary and sources rather than leaving fields blank."
    )


def build_comparable_deals_prompt(
    company_name: str,
    category_keywords: list[str],
    stage: Stage,
    product_summary: str,
) -> str:
    """Construct the user-prompt body for the comparable-deals call."""
    keywords = ", ".join(category_keywords) if category_keywords else "(none provided)"
    return (
        f"Target company: {company_name}\n"
        f"Stage: {stage.value}\n"
        f"Category keywords: {keywords}\n"
        f"Product summary: {product_summary}\n\n"
        "Find 2-4 real comparables in the same category + stage. Run the "
        "WebSearches specified in the system prompt. Return JSON matching "
        "the ComparableDeals schema. Each comp must have at least one "
        "verifiable source — if you can't find anything, return fewer "
        "comps rather than fabricating."
    )


# ---------------------------------------------------------------------------
# Caching helpers.
# ---------------------------------------------------------------------------


FOUNDER_PROFILES_FILENAME = ".founder_profiles_cache.json"
COMPARABLE_DEALS_FILENAME = ".comparable_deals_cache.json"


class _FounderProfileList(BaseModel):
    """Wrapper so we can JSON-roundtrip a list[FounderProfile]."""

    model_config = ConfigDict(frozen=True)
    profiles: list[FounderProfile]


def load_or_profile_founders(
    folder: Path, founder_names: list[str], company_name: str
) -> list[FounderProfile]:
    """Cached founder profiling. Returns an empty list if no founders are
    known (AL didn't surface team and no deck was supplied)."""
    if not founder_names:
        return []
    cache_path = folder / FOUNDER_PROFILES_FILENAME
    if cache_path.is_file():
        return _FounderProfileList.model_validate_json(
            cache_path.read_text(encoding="utf-8")
        ).profiles
    profiles = [profile_founder(name, company_name) for name in founder_names]
    cache_path.write_text(
        _FounderProfileList(profiles=profiles).model_dump_json(indent=2),
        encoding="utf-8",
    )
    return profiles


def load_or_find_comps(
    folder: Path,
    company_name: str,
    category_keywords: list[str],
    stage: Stage,
    product_summary: str,
) -> ComparableDeals | None:
    """Cached comparable-deals lookup. Returns None if the call returns
    fewer than 2 comps (caller treats that as "no usable comps found")."""
    cache_path = folder / COMPARABLE_DEALS_FILENAME
    if cache_path.is_file():
        return ComparableDeals.model_validate_json(cache_path.read_text(encoding="utf-8"))
    result = find_comparable_deals(company_name, category_keywords, stage, product_summary)
    cache_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return result


# ---------------------------------------------------------------------------
# Recent events — the discovery pass the claims-level brief lacks.
# ---------------------------------------------------------------------------

RECENT_EVENTS_FILENAME = ".recent_events_cache.json"

# Events go stale in a way founder pedigrees don't; re-research after this.
_EVENTS_MAX_AGE_DAYS = 30


class CompanyEvent(BaseModel):
    """One thesis-material event found on the public web."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    date: str = Field(min_length=1)  # "2026-01" / "Jan 2026" — as precise as sourced
    headline: str = Field(min_length=1)  # one line, factual
    why_material: str = Field(min_length=1)  # ties the event to the investment thesis
    source: str = Field(min_length=1)
    source_type: Literal["primary", "secondary"]


class RecentEvents(BaseModel):
    """Web-researched event sweep for one company and its buyer/competitor
    orbit. `searches_run` makes an empty result evidenced ("nothing found
    after searching X, Y") instead of indistinguishable from "never looked" —
    the exact gap that let a CEO's 'not ready for prime time' quote and a
    buyer's insourcing acquisition go unseen by a claims-level brief."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    company: str = Field(min_length=1)
    as_of: str = Field(min_length=1)  # date the sweep ran, "YYYY-MM-DD"
    events: list[CompanyEvent] = Field(default=[], max_length=6)
    searches_run: list[str] = Field(min_length=1, max_length=8)


_RECENT_EVENTS_SYSTEM_PROMPT = """You are sweeping the last ~18 months of
public record for events MATERIAL to an angel investment in a target
company. The diligence brief already analyzes the company's own claims;
your job is what the claims don't volunteer.

REQUIRED searches (adapt names; record each in `searches_run`):
  1. "<company> news <last year> <this year>"
  2. "<company> CEO OR founder interview"
  3. "<anchor customer> <category> automation strategy" — what the
     customer's OWN executives say about the technology's maturity and
     their vendor strategy
  4. "<category> acquisition OR insourcing OR shutdown <this year>"
  5. "<top competitor> funding OR contract <this year>"

MATERIAL means it could move the investment decision:
  - Executive quotes about maturity/timelines (a customer CEO saying
    "pilot stage, not ready" outweighs any deck claim)
  - A buyer insourcing (acquiring its own capability = exiting the
    customer pool)
  - Competitor funding/M&A that re-prices the category (a take-private
    at a fraction of the target's mark)
  - Customer wins/losses, exec departures, safety/regulatory incidents

NOT material (do not include): product-award PR, minor partnership
announcements, listicles, opinion pieces without new facts.

Each event: one-line factual headline, a date as precise as the source
supports, why_material tied to THIS deal's thesis, and its source.
Cap at the 6 most material. If the sweep finds nothing material, return
an empty events list — searches_run proves the sweep happened."""


def find_recent_events(
    company_name: str,
    category_keywords: list[str],
    anchor_names: list[str],
) -> RecentEvents:
    """Web-sweep material events for one company. ~$0.10 / 60s per call."""
    prompt = build_recent_events_prompt(company_name, category_keywords, anchor_names)
    return extract_structured(
        prompt,
        RecentEvents,
        purpose=Purpose.RECENT_EVENTS,
        system_prompt=_RECENT_EVENTS_SYSTEM_PROMPT,
    )


def load_or_find_events(
    folder: Path,
    company_name: str,
    category_keywords: list[str],
    anchor_names: list[str],
) -> RecentEvents | None:
    """Cached event sweep, re-run when the cache is older than
    `_EVENTS_MAX_AGE_DAYS` — events, unlike founder pedigrees, decay."""
    cache_path = folder / RECENT_EVENTS_FILENAME
    if cache_path.is_file():
        cached = RecentEvents.model_validate_json(cache_path.read_text(encoding="utf-8"))
        try:
            age = (date.today() - date.fromisoformat(cached.as_of)).days
        except ValueError:
            age = _EVENTS_MAX_AGE_DAYS + 1  # unparsable as_of: refresh
        if age <= _EVENTS_MAX_AGE_DAYS:
            return cached
    result = find_recent_events(company_name, category_keywords, anchor_names)
    cache_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return result


def build_recent_events_prompt(
    company_name: str,
    category_keywords: list[str],
    anchor_names: list[str],
) -> str:
    """Construct the user-prompt body for the event sweep."""
    anchors = ", ".join(anchor_names) if anchor_names else "(none named)"
    return (
        f"Target company: {company_name}\n"
        f"Category: {', '.join(category_keywords)}\n"
        f"Anchor customers / co-investors to sweep: {anchors}\n"
        f"Today's date for as_of: {date.today().isoformat()}\n\n"
        "Run the searches specified in the system prompt. Return JSON "
        "matching the RecentEvents schema; record every search in "
        "searches_run."
    )


def render_recent_events_text(events: RecentEvents | None) -> str:
    """Render the event sweep as dense text for the synthesis prompt."""
    if events is None:
        return "RECENT EVENTS: (sweep unavailable)"
    parts = [f"RECENT EVENTS (web-swept {events.as_of}):"]
    if not events.events:
        parts.append("  No material events found. Searches run: " + "; ".join(events.searches_run))
        return "\n".join(parts)
    for e in events.events:
        parts.append(f"  - [{e.date}] {e.headline} ({e.source_type}: {e.source})")
        parts.append(f"      Why material: {e.why_material}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Text rendering — used by the synthesis pass to consume research output.
# ---------------------------------------------------------------------------


def render_founder_profiles_text(profiles: list[FounderProfile]) -> str:
    """Render a list of `FounderProfile` as dense text for the synthesis
    prompt. Mirrors the existing `DeckContent` rendering pattern."""
    if not profiles:
        return "Founder profiles: (no founders surfaced in AL / deck)"
    parts: list[str] = ["FOUNDER PROFILES (web-researched):"]
    for p in profiles:
        parts.append(f"  - {p.name} ({p.role or 'role unknown'}) — Tier {p.pedigree_tier}")
        parts.append(f"      Justification: {p.pedigree_justification}")
        if p.prior_employers:
            parts.append("      Prior employers:")
            for e in p.prior_employers:
                outcome = f" → {e.outcome}" if e.outcome else ""
                parts.append(f"        * {e.company} ({e.role}){outcome}")
        if p.education:
            parts.append(f"      Education: {'; '.join(p.education)}")
        if p.notable_outcomes:
            parts.append("      Notable outcomes:")
            for o in p.notable_outcomes:
                parts.append(f"        * {o}")
        parts.append(f"      Summary: {p.web_research_summary}")
        if p.linkedin_url:
            parts.append(f"      LinkedIn: {p.linkedin_url}")
        parts.append(f"      Sources: {'; '.join(p.sources)}")
    return "\n".join(parts)


def render_comparable_deals_text(comps: ComparableDeals | None) -> str:
    """Render a `ComparableDeals` as dense text for the synthesis prompt."""
    if comps is None or not comps.comps:
        return "Comparable deals: (none found via web search)"
    parts: list[str] = [
        f"COMPARABLE DEALS in {comps.category} (web-researched):",
        f"  Read on comp set: {comps.summary}",
    ]
    for c in comps.comps:
        parts.append(f"  - {c.company_name} ({c.stage}) — {c.category_fit}")
        money_bits: list[str] = []
        if c.last_round_usd is not None:
            money_bits.append(f"last round ${c.last_round_usd:,.0f}")
        if c.valuation_usd is not None:
            money_bits.append(f"valuation ${c.valuation_usd:,.0f}")
        if c.arr_usd is not None:
            money_bits.append(f"ARR ${c.arr_usd:,.0f}")
        if c.valuation_multiple is not None:
            basis = c.multiple_basis or "multiple"
            as_of = f" as of {c.multiple_as_of}" if c.multiple_as_of else " (UNDATED)"
            money_bits.append(f"{c.valuation_multiple:g}x {basis}{as_of}")
        if c.last_round_date:
            money_bits.append(c.last_round_date)
        if money_bits:
            parts.append(f"      {' · '.join(money_bits)}")
        if c.co_investors:
            parts.append(f"      Co-investors: {', '.join(c.co_investors)}")
        if c.notes:
            parts.append(f"      Notes: {c.notes}")
        parts.append(f"      Sources: {'; '.join(c.sources)}")
    return "\n".join(parts)
