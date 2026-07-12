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

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from angel_memos.claude_cli import OPUS_MODEL, extract_structured
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
        model=OPUS_MODEL,
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
        model=OPUS_MODEL,
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
