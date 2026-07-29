"""Adversarial decision review.

Run after `decision.md` is written, before Phase B memo generation. Reads
the decision + materials and asks Claude to pressure-test the decision —
steelman the opposite verdict, identify weaknesses in reasoning, flag
missing considerations, recommend follow-up actions.

The output (`decision_review.md`) is a working document the user reads to
decide whether to iterate on `decision.md` before committing the memo.
"""

from pathlib import Path

from angel_memos.claude import Purpose, call_llm
from angel_memos.deck import parse_deck_content
from angel_memos.materials import Materials, load_materials, read_text
from angel_memos.models import AngelListMetadata, Decision, DeckContent

_REVIEW_SYSTEM_PROMPT = """You are a senior investor reviewing another
investor's stated decision on a private deal. Your sole job in this pass is
to ADVERSARIALLY pressure-test the decision — surface weaknesses in their
reasoning, biases that may have shaped the call, and considerations they
appear to have underweighted or missed.

DO NOT:
- Restate the decision back to them
- Summarize the deal materials
- Affirm the verdict ("good call!" "well-reasoned" etc.)
- Give a five-section memo

DO produce a tight, sharp critique structured as follows (Markdown):

# Adversarial Review

## Steelman the opposite verdict
2-3 paragraphs constructing the strongest version of the OPPOSITE decision
(if they're passing, argue for buying; if they're buying, argue for passing).
Use specifics from the materials. This is the case the user did not make to
themselves.

## Weaknesses in the stated reasoning
3-5 bullets. Each should name a specific claim or assumption from the user's
`top_reasons` / `raw_reasoning` and surface a falsifiable challenge to it.
"You assumed X; here's why X might be wrong" — not "consider whether X."

## Missing considerations
2-3 bullets identifying important factors the decision didn't address —
e.g., a competitor not named, a regulatory risk not flagged, a base-rate
analysis not done, a key-person risk not considered. Be specific.

## Bias and process checks
1-3 bullets calling out potential cognitive biases (recency, narrative,
team-attraction, valuation-anchoring) and process failures (insufficient
time, conflict of interest, insufficient expert references).

## Follow-up actions to firm up conviction
2-3 specific, actionable items the user could do in <5 hours of effort that
would most change the calibration of the decision.

## Bottom line
One paragraph: is the decision DEFENSIBLE AS-IS given the available
materials and stated rationale, or does it need rework? Be direct. Use
"defensible," "marginal," or "needs rework" as the verdict word."""


def run_decision_review(folder: Path) -> Path:
    """End-to-end adversarial review of `<folder>/decision.md`. Writes
    `<folder>/decision_review.md` and returns the path."""
    from angel_memos.memo import parse_decision

    decision_path = folder / "decision.md"
    if not decision_path.is_file():
        raise FileNotFoundError(f"decision.md not found in {folder}. Run /angel-decide first.")

    from angel_memos.memo import load_or_parse_angellist

    decision = parse_decision(decision_path)
    materials = load_materials(folder)
    angellist = load_or_parse_angellist(folder, materials)
    deck_path = materials.deck.path if materials.deck is not None else None
    deck_content = parse_deck_content(deck_path) if deck_path is not None else None

    prompt = _build_review_prompt(decision, angellist, deck_content, materials)
    review_md = call_llm(
        prompt,
        purpose=Purpose.DECISION_REVIEW,
        timeout_seconds=600,
    )

    out_path = folder / "decision_review.md"
    out_path.write_text(review_md, encoding="utf-8")
    return out_path


def _build_review_prompt(
    decision: Decision,
    angellist: AngelListMetadata,
    deck_content: DeckContent | None,
    materials: Materials,
) -> str:
    """Compose a text-heavy prompt for the review pass. We don't re-send
    the rasterized PDFs — the deal facts come from the pre-parsed AL +
    deck content, and the diligence_topics.html (if present) is included
    as additional context the user already reviewed."""
    diligence_path = materials.folder / "diligence_topics.html"
    diligence_excerpt = ""
    if diligence_path.is_file():
        diligence_excerpt = diligence_path.read_text(encoding="utf-8")

    notes_text = "\n\n".join(f"--- {n.path.name} ---\n{read_text(n)}" for n in materials.notes)

    al_summary = (
        f"AngelList metadata (pre-parsed):\n"
        f"  Company: {angellist.company}\n"
        f"  Round: {angellist.round_label}\n"
        f"  Markets: {', '.join(angellist.markets)}\n"
        f"  Pre-money: ${angellist.pre_money_usd:,.0f}\n"
        f"  Round size: ${angellist.estimated_round_size_usd:,.0f}\n"
        f"  Post-money: ${angellist.post_money_usd:,.0f}\n"
        f"  Founders: {', '.join(angellist.founders) if angellist.founders else 'unknown'}\n"
        f"  Co-investors: "
        f"{', '.join(angellist.co_investors) if angellist.co_investors else 'none disclosed'}\n"
    )

    deck_summary = ""
    if deck_content is not None:
        deck_summary = (
            "Pitch-deck content (pre-parsed):\n"
            f"  Product: {deck_content.product_description}\n"
            f"  ICP: {deck_content.icp}\n"
            f"  Wedge: {deck_content.primary_use_case}\n"
            f"  Traction: {deck_content.traction or '(undisclosed)'}\n"
            f"  Competitors mentioned: "
            f"{', '.join(deck_content.competitors_mentioned) if deck_content.competitors_mentioned else '(none)'}\n"
            f"  GTM: {deck_content.gtm_motion or '(undisclosed)'}\n"
            f"  Differentiation: {deck_content.differentiation or '(undisclosed)'}\n"
        )

    decision_summary = (
        f"USER DECISION:\n"
        f"  Verdict: {decision.verdict.value}\n"
        f"  Conviction: {decision.conviction.value}\n"
        f"  Check: ${decision.check_usd:,.0f}\n"
        f"  Post-money: ${decision.post_money_usd:,.0f}\n"
        f"  Valuation method: {decision.valuation_method.value}\n"
        f"  Top reasons:\n"
        + "".join(f"    - {r}\n" for r in decision.top_reasons)
        + "  Top risks (accepted or foregone):\n"
        + "".join(f"    - {r}\n" for r in decision.top_risks)
        + f"\n  Raw reasoning:\n  {decision.raw_reasoning}\n"
    )

    sections: list[str] = [al_summary]
    if deck_summary:
        sections.append(deck_summary)
    if diligence_excerpt:
        sections.extend(["Diligence topics (HTML, for reference):", diligence_excerpt[:8000]])
    sections.append(decision_summary)
    if notes_text:
        sections.append("Freeform notes:")
        sections.append(notes_text)
    sections.append(
        "\nProduce the adversarial review per the system prompt. Be sharp, "
        "specific, and constructive. The user is paying you to find what "
        "they missed."
    )
    return "\n\n".join(sections)
