"""Pitch deck parser.

Extracts structured `DeckContent` (product, ICP, traction, etc.) from a
deck PDF in a single focused Claude vision call. Downstream diligence and
memo prompts consume the resulting text rather than re-reading the deck
images — empirically, when Claude is given 30+ image attachments alongside
a complex synthesis schema, it skims; small focused vision calls + text-
heavy synthesis calls produce reliably deep output.
"""

import tempfile
from pathlib import Path

from angel_memos.claude_cli import OPUS_MODEL, extract_structured
from angel_memos.models import DeckContent
from angel_memos.pdf_utils import rasterize_pdf

_SYSTEM_PROMPT = """You extract structured content from a startup's pitch
deck for use in downstream investment diligence. The deck is attached as
PNG images, one per slide.

READ EVERY SLIDE before responding. Don't skim. Don't say "deck unreadable"
— if a slide has content, transcribe what's there.

Field rules:
- product_description: be concrete. "AI company" is wrong; "AI-powered
  legal contract review for in-house counsel at mid-market SaaS companies"
  is right. Include the workflow being replaced or augmented.
- icp: the buyer persona AND the customer-size profile (e.g., "Director of
  Engineering at 50-500 person SaaS companies", or "Solo / small-firm
  attorneys handling commercial agreements")
- primary_use_case: the wedge problem the deck claims to solve, in one or
  two sentences
- pricing_model: subscription / usage / transaction take / hybrid; include
  ACV bands if shown
- traction: every quantitative traction claim — ARR, customer count, logos,
  design partners, growth %, retention, NDR. If undisclosed, say so.
- competitors_mentioned: explicit competitor names the deck calls out
- market_claims: TAM/SAM/SOM language verbatim or paraphrased
- gtm_motion: how the company sells today (founder-led, inbound PLG,
  outbound sales, channel partnerships)
- roadmap: stated milestones or product expansion plans
- differentiation: the moat thesis stated in the deck (data, distribution,
  workflow lock-in, model IP, etc.)
- notable_metrics: any other quantitative claims worth surfacing for
  diligence (NRR, payback, cohort retention, model evals, etc.)

If a field genuinely isn't on the deck, return an empty string for string
fields or empty list for list fields — do NOT invent content."""


def parse_deck_content(deck_pdf: Path) -> DeckContent:
    """Read a deck PDF via Claude vision and return structured content.

    Raises:
      FileNotFoundError: `deck_pdf` does not point to a real file.
      pydantic.ValidationError: Claude returned JSON that does not satisfy
        the `DeckContent` schema.
    """
    if not deck_pdf.is_file():
        raise FileNotFoundError(f"Deck PDF not found: {deck_pdf}")

    with tempfile.TemporaryDirectory(prefix="angel_memos_deck_") as tmp_str:
        tmp_dir = Path(tmp_str)
        pages = rasterize_pdf(deck_pdf, tmp_dir / "deck")
        refs = "\n".join(f"@{p.as_posix()}" for p in pages)
        prompt = (
            f"{refs}\n\n"
            "These are slides of a startup's pitch deck, in order. Extract the "
            "structured content per the schema. Read every slide before "
            "responding — do NOT skim or claim slides are unreadable."
        )
        return extract_structured(
            prompt,
            DeckContent,
            model=OPUS_MODEL,
            system_prompt=_SYSTEM_PROMPT,
            additional_dirs=[str(tmp_dir)],
        )
