"""Structured doc-entry models and the Claude-driven generators that produce
them in Bhanu's voice.

Two outputs:

  - `PrivateDocEntry`: terse bullet shorthand for the `[Private] Angel
    Investing` doc. Every decision (buy / hold / pass) gets one. Pass
    entries are prefixed `[Passed]` in the company heading; the second
    section is labeled "Why Passing?" instead of "Risks".

  - `PublicDocEntry`: a structured one-page deal memo for the
    `[Public] Investing Memos` doc. Generated only for buy/strong_buy
    verdicts; anonymized at generation time (category descriptor, no
    founder names, bucketed dollar amounts).

Both are produced via `extract_structured` with the corresponding style
guide loaded as the system prompt. The deterministic helpers in this
module (`private_company_heading`, `render_public_entry_markdown`,
`build_*_prompt`) are pure functions that can be unit-tested without
calling Claude.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from angel_memos.claude import Purpose, extract_structured
from angel_memos.deck import parse_deck_content
from angel_memos.materials import Materials, read_text
from angel_memos.models import (
    AngelListMetadata,
    Decision,
    DeckContent,
    Verdict,
)

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_PRIVATE_STYLE_PATH = _PROMPTS_DIR / "private_doc_style.md"
_PUBLIC_STYLE_PATH = _PROMPTS_DIR / "public_doc_style.md"


# ---------------------------------------------------------------------------
# Private-doc entry: terse bullets.
# ---------------------------------------------------------------------------


class PrivateDocEntry(BaseModel):
    """One private-doc entry in bullet shorthand.

    The publish flow consumes this to render under the appropriate H2
    container (Portfolio for buys, Passed Deals for pass/hold) with the
    appropriate company heading (`[Passed]` prefix for non-buys) and
    second-section label ("Risks" for buys, "Why Passing?" otherwise).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    rationale_bullets: list[str] = Field(min_length=2, max_length=12)
    second_section_bullets: list[str] = Field(min_length=1, max_length=10)


def private_company_heading(decision: Decision) -> str:
    """Return the H3 company heading text for the private doc.

    Buys: `<Company>`. Pass/hold: `[Passed] <Company>` — matches Bhanu's
    convention in the existing doc (`[Passed] Anode`, etc.).
    """
    if decision.verdict in {Verdict.BUY, Verdict.STRONG_BUY}:
        return decision.company
    return f"[Passed] {decision.company}"


def private_second_section_label(decision: Decision) -> str:
    """Return the H4 label for the second sub-section of a private entry."""
    if decision.verdict in {Verdict.BUY, Verdict.STRONG_BUY}:
        return "Risks"
    return "Why Passing?"


# ---------------------------------------------------------------------------
# Public-doc entry: full structured memo.
# ---------------------------------------------------------------------------


class MarketAndOpportunity(BaseModel):
    """Market & Opportunity section of a public memo."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    job_to_be_done: str = Field(min_length=1)
    market_size: str = Field(min_length=1)
    why_now: str = Field(min_length=1)


class TeamSection(BaseModel):
    """Team section of a public memo. Founders referred to by title only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    founder_market_fit: str = Field(min_length=1)
    superpower_or_execution_advantage: str = Field(min_length=1)


class KeyMetricsSection(BaseModel):
    """Key Metrics (Anonymized Ranges) section. All values bucketed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    arr_or_contracted_revenue: str = Field(min_length=1)
    retention: str = Field(min_length=1)
    efficiency_or_techno_economics: str = Field(min_length=1)


class CompetitiveMoatSection(BaseModel):
    """Optional Competitive Moat & Company Superpower section."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    structural_advantage: str = Field(min_length=1)
    execution_velocity: str = Field(min_length=1)


class BullCaseSection(BaseModel):
    """Bull Case section ending in 'Verdict: GO.' followed by the rationale."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    thesis: str = Field(min_length=1)
    verdict: str = Field(min_length=1)  # "GO. ..." text per style guide


class PublicDocEntry(BaseModel):
    """A full anonymized public-memo entry. Produced only for buy/strong_buy.

    All identifying details are anonymized at generation time per the
    public-doc style guide:
      - company name → category descriptor (3-7 words)
      - founder names → titles only
      - dollar amounts → bucketed ranges
      - customer names → tier + size descriptors
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    category_descriptor: str = Field(min_length=1, max_length=80)
    stage_label: str = Field(min_length=1)  # e.g. "Seed", "Series A", "Growth"
    date_label: str = Field(min_length=1)  # e.g. "May 2026"
    what_does_it_do: str = Field(min_length=1)
    why_is_it_important: str = Field(min_length=1)
    market_and_opportunity: MarketAndOpportunity
    team: TeamSection
    key_metrics: KeyMetricsSection
    competitive_moat: CompetitiveMoatSection | None = None
    anti_thesis_paragraphs: list[str] = Field(min_length=2, max_length=4)
    bull_case: BullCaseSection
    private_only_terms: list[str] = Field(default_factory=list, max_length=80)
    approval_sha256: str | None = Field(default=None, min_length=64, max_length=64)


def public_entry_heading(entry: PublicDocEntry) -> str:
    """H4 heading for a public entry per the style guide:
    `<Category Descriptor> — <Stage> Deal Memo`."""
    return f"{entry.category_descriptor} — {entry.stage_label} Deal Memo"


# ---------------------------------------------------------------------------
# Markdown rendering for local artifacts (`memo_public.md`).
# ---------------------------------------------------------------------------


def render_public_entry_markdown(entry: PublicDocEntry) -> str:
    """Render a `PublicDocEntry` as Markdown for local inspection.

    Mirrors the structure that gets written to the Google Doc so the local
    file is a faithful preview of the published entry.
    """
    out: list[str] = [
        f"#### {public_entry_heading(entry)}",
        "",
        f"Date: {entry.date_label}",
        "",
        f"**What does it do?** {entry.what_does_it_do}",
        "",
        f"**Why is it important?** {entry.why_is_it_important}",
        "",
        "**Market & Opportunity**",
        f"  * Job(s) to be done: {entry.market_and_opportunity.job_to_be_done}",
        f"  * Market Size: {entry.market_and_opportunity.market_size}",
        f"  * Why Now?: {entry.market_and_opportunity.why_now}",
        "",
        "**Team**",
        f"  * Founder Market Fit: {entry.team.founder_market_fit}",
        (f"  * Superpower or Execution Advantage: {entry.team.superpower_or_execution_advantage}"),
        "",
        "**Key Metrics (Anonymized Ranges)**",
        f"  * ARR / Contracted Revenue: {entry.key_metrics.arr_or_contracted_revenue}",
        f"  * Retention: {entry.key_metrics.retention}",
        (f"  * Efficiency / Techno-Economics: {entry.key_metrics.efficiency_or_techno_economics}"),
    ]
    if entry.competitive_moat is not None:
        out.extend(
            [
                "",
                "**Competitive Moat & Company Superpower**",
                f"  * The Structural Advantage: {entry.competitive_moat.structural_advantage}",
                f"  * Execution Velocity: {entry.competitive_moat.execution_velocity}",
            ]
        )
    out.append("")
    out.append("**Anti-Thesis**")
    for para in entry.anti_thesis_paragraphs:
        out.append(f"  * {para}")
    out.extend(
        [
            "",
            "**Bull Case**",
            f"  * Thesis: {entry.bull_case.thesis}",
            f"  * Verdict: {entry.bull_case.verdict}",
        ]
    )
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Claude generators.
# ---------------------------------------------------------------------------


def generate_private_entry(
    decision: Decision,
    angellist: AngelListMetadata,
    deck_content: DeckContent | None,
) -> PrivateDocEntry:
    """Generate a private-doc entry in Bhanu's bullet shorthand.

    Uses `private_doc_style.md` as the system prompt so the output matches
    the voice anchored by Bhanu's existing entries (Zeno Moto, [Passed]
    Anode, etc.). Single Claude call; ~$0.05 / 20s.
    """
    system_prompt = _PRIVATE_STYLE_PATH.read_text(encoding="utf-8")
    prompt = build_private_entry_prompt(decision, angellist, deck_content)
    return extract_structured(
        prompt,
        PrivateDocEntry,
        purpose=Purpose.PRIVATE_DOC_ENTRY,
        system_prompt=system_prompt,
    )


def generate_public_entry(
    decision: Decision,
    angellist: AngelListMetadata,
    materials: Materials,
    today: date,
) -> PublicDocEntry:
    """Generate a public-doc entry. Buys only — the publish flow guards.

    Pre-parses the deck (if present) into structured text so the generator
    can ground Market & Opportunity / Team / Key Metrics in deck content
    rather than re-skimming the images.
    """
    deck_content: DeckContent | None = None
    if materials.deck is not None:
        deck_content = parse_deck_content(materials.deck.path)
    system_prompt = _PUBLIC_STYLE_PATH.read_text(encoding="utf-8")
    prompt = build_public_entry_prompt(decision, angellist, deck_content, materials, today)
    return extract_structured(
        prompt,
        PublicDocEntry,
        purpose=Purpose.PUBLIC_DOC_ENTRY,
        system_prompt=system_prompt,
    )


# ---------------------------------------------------------------------------
# Prompt builders — pure functions, unit-testable.
# ---------------------------------------------------------------------------


def build_private_entry_prompt(
    decision: Decision,
    angellist: AngelListMetadata,
    deck_content: DeckContent | None,
) -> str:
    """Construct the user-prompt body for the private-entry generator.

    Includes decision facts (verdict, conviction, top_reasons, top_risks,
    raw_reasoning), parsed AL terms (pre-money, co-investors, founders,
    markets), and deck synthesis if available.
    """
    lines: list[str] = [
        f"Generate a private-doc entry for {decision.company}.",
        f"Verdict: {decision.verdict.value} "
        f"(use `{private_company_heading(decision)}` as the company heading).",
        f"Conviction: {decision.conviction.value}",
        "",
        "USER INPUTS (from decision.md):",
    ]
    if decision.verdict not in {Verdict.PASS, Verdict.HOLD}:
        lines.append(f"  Check: ${decision.check_usd:,.0f}")
        lines.append(f"  Post-money: ${decision.post_money_usd:,.0f}")
    lines.append("  Top reasons:")
    lines.extend(f"    - {r}" for r in decision.top_reasons)
    lines.append("  Top risks:")
    lines.extend(f"    - {r}" for r in decision.top_risks)
    lines.append("  Raw reasoning:")
    for raw_line in decision.raw_reasoning.strip().splitlines():
        lines.append(f"    {raw_line}")

    lines.extend(
        [
            "",
            "DEAL FACTS (from AngelList memo):",
            f"  Company (AL label): {angellist.company}",
            f"  Round: {angellist.round_label} (stage: {angellist.stage.value})",
            f"  Pre-money: ${angellist.pre_money_usd:,.0f}",
            f"  Post-money: ${angellist.post_money_usd:,.0f}",
            f"  Markets: {', '.join(angellist.markets)}",
            f"  Founders: {', '.join(angellist.founders) if angellist.founders else '(not surfaced)'}",
            f"  Co-investors: "
            f"{', '.join(angellist.co_investors) if angellist.co_investors else 'none disclosed'}",
        ]
    )
    if angellist.total_prior_capital_usd is not None:
        lines.append(f"  Total prior capital: ${angellist.total_prior_capital_usd:,.0f}")

    if deck_content is not None:
        lines.extend(
            [
                "",
                "PITCH-DECK SYNTHESIS:",
                f"  Product: {deck_content.product_description}",
                f"  ICP: {deck_content.icp}",
                f"  Primary use case: {deck_content.primary_use_case}",
                f"  Traction: {deck_content.traction or '(not disclosed in deck)'}",
                f"  Differentiation: {deck_content.differentiation or '(not disclosed in deck)'}",
            ]
        )
        if deck_content.notable_metrics:
            lines.append("  Notable metrics:")
            lines.extend(f"    - {m}" for m in deck_content.notable_metrics)

    if decision.verdict in {Verdict.BUY, Verdict.STRONG_BUY}:
        second_label = "Risks"
        section_hint = (
            "rationale_bullets = the deal facts + bull observations + what's "
            "interesting; second_section_bullets = the specific failure modes "
            "you accepted by committing (Bhanu's Risks shorthand — concise, "
            "specific, often single words like 'Execution' or short phrases)."
        )
    else:
        second_label = "Why Passing?"
        section_hint = (
            "rationale_bullets = the deal facts + 1-2 bullets on what's "
            "interesting about the deal (the bull case Bhanu acknowledged "
            "exists); second_section_bullets = the specific reasons for the "
            "no (valuation observations, stage-fit concerns, operational "
            "concerns, honest self-assessment, strategic logic). Mix these "
            "concrete categories per the style guide."
        )

    lines.extend(
        [
            "",
            f"Output the JSON for a PrivateDocEntry. The second section will be "
            f"rendered under the heading '{second_label}'.",
            section_hint,
            "Every bullet is one line. Most are observations or facts (`$XXM Pre`, "
            "`Co-Investors: Eclipse`, `Pretty Rich`), not paragraphs. Honest "
            "first-person register is allowed where it fits the voice. Use real "
            "names and real numbers — this is the private doc.",
        ]
    )
    return "\n".join(lines)


def build_public_entry_prompt(
    decision: Decision,
    angellist: AngelListMetadata,
    deck_content: DeckContent | None,
    materials: Materials,
    today: date,
) -> str:
    """Construct the user-prompt body for the public-entry generator.

    Provides the same parsed AL + deck context as the private path, plus
    explicit instructions on anonymization (category descriptor, titles
    only, bucketed dollars). The system prompt is the public-doc style
    guide.
    """
    date_label = today.strftime("%B %Y")
    lines: list[str] = [
        "Generate a public-memo entry for the company described below. "
        "Anonymize per the style guide:",
        "  - Replace the company name with a category descriptor (3-7 "
        "    words, distinctive enough to convey the lane without "
        "    identifying the company).",
        "  - Refer to founders by title only (no names).",
        "  - Bucket dollar amounts (low/mid/high $XM CARR, sub-$XXM post, "
        "    >10x NTM EV/Rev, etc.).",
        "  - Public co-investor names are fine; specific customer names "
        "    are not (use tier + size descriptors).",
        "",
        f"Set `date_label` to '{date_label}'. Set `stage_label` from the "
        f"round below (Seed, Series A, Series B+, Growth — match the "
        f"convention).",
        "",
        "DEAL FACTS (from AngelList memo):",
        f"  Real company name (for context only — DO NOT include): {decision.company}",
        f"  Round: {angellist.round_label} (stage: {angellist.stage.value})",
        f"  Pre-money: ${angellist.pre_money_usd:,.0f}",
        f"  Round size: ${angellist.estimated_round_size_usd:,.0f}",
        f"  Post-money: ${angellist.post_money_usd:,.0f}",
        f"  Markets: {', '.join(angellist.markets)}",
        f"  Co-investors: "
        f"{', '.join(angellist.co_investors) if angellist.co_investors else 'none disclosed'}",
    ]
    if angellist.total_prior_capital_usd is not None:
        lines.append(f"  Total prior capital: ${angellist.total_prior_capital_usd:,.0f}")
    if angellist.founders:
        lines.append(f"  Founders (use roles, NOT names): {', '.join(angellist.founders)}")

    lines.extend(
        [
            "",
            "USER DECISION (from decision.md — Bhanu's framing):",
            f"  Verdict: {decision.verdict.value}",
            f"  Conviction: {decision.conviction.value}",
            f"  Check: ${decision.check_usd:,.0f} (anonymize to bucket)",
            f"  Post-money: ${decision.post_money_usd:,.0f} (anonymize to bucket)",
            f"  Valuation method: {decision.valuation_method.value}",
            "  Top reasons:",
        ]
    )
    lines.extend(f"    - {r}" for r in decision.top_reasons)
    lines.append("  Top risks accepted:")
    lines.extend(f"    - {r}" for r in decision.top_risks)
    lines.append("  Raw reasoning:")
    for raw_line in decision.raw_reasoning.strip().splitlines():
        lines.append(f"    {raw_line}")

    if decision.scenarios:
        lines.append("  Scenarios (use to anchor Bull Case math):")
        for s in decision.scenarios:
            lines.append(f"    - {s.model_dump_json()}")
    if decision.benchmarks:
        lines.append("  Benchmarks (use to anchor exit-math discussion):")
        for b in decision.benchmarks:
            lines.append(f"    - {b.model_dump_json()}")

    if deck_content is not None:
        lines.extend(
            [
                "",
                "PITCH-DECK SYNTHESIS (use for Market & Opportunity / Team / Key Metrics):",
                f"  Product: {deck_content.product_description}",
                f"  ICP: {deck_content.icp}",
                f"  Primary use case: {deck_content.primary_use_case}",
                f"  Pricing: {deck_content.pricing_model or '(not disclosed)'}",
                f"  Traction: {deck_content.traction or '(not disclosed)'}",
                f"  Competitors mentioned: "
                f"{', '.join(deck_content.competitors_mentioned) if deck_content.competitors_mentioned else '(none)'}",
                f"  Market claims: {deck_content.market_claims or '(none disclosed)'}",
                f"  GTM motion: {deck_content.gtm_motion or '(not disclosed)'}",
                f"  Differentiation: {deck_content.differentiation or '(not disclosed)'}",
            ]
        )
        if deck_content.notable_metrics:
            lines.append("  Notable metrics:")
            lines.extend(f"    - {m}" for m in deck_content.notable_metrics)

    notes_text = "\n\n".join(f"--- {n.path.name} ---\n{read_text(n)}" for n in materials.notes)
    if notes_text:
        lines.extend(["", "Freeform notes from Bhanu's folder:", notes_text])

    lines.extend(
        [
            "",
            "Output the JSON for a PublicDocEntry. Each section gets the "
            "punchy declarative voice anchored by Bhanu's existing entries "
            "(Emerging Market EV, Video Management Agents, Next-Gen Zero-"
            "Emission Power Turbines). Anti-Thesis bullets are full paragraphs "
            "that walk through specific failure mechanisms — NOT generic risk "
            "labels. Bull Case ends with `Verdict: GO. <one sentence on entry-"
            "price defensibility>`. Where you make an inference Bhanu didn't "
            "surface, prefix it `[NEEDS BHANU REVIEW: <inference>]`.",
            "The `private_only_terms` field must list every customer, entity alias, "
            "URL/domain, email, or distinctive private phrase that must not reach "
            "the public document. Keep it empty only when none exist.",
        ]
    )
    return "\n".join(lines)
