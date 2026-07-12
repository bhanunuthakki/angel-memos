"""AngelList memo parser.

Extracts `AngelListMetadata` via TWO focused Claude vision calls:

  1. Terms extraction — reads the AL memo pages and pulls structured deal
     terms (round, valuation, fees, carry, allocation, markets, co-
     investors, prior capital).
  2. Founders extraction — reads the pitch deck team slide (or, if no deck,
     the middle pages of the AL memo) and pulls the founder roster.

Why two passes rather than one big call: empirically, with 30+ attached
images Claude skims rather than reading deeply, and produces placeholder
values like `founders: ["Unknown"]`. Splitting into focused calls (each
seeing ~15 images) restores reliable extraction. Each call costs ~$0.10
on Opus 4.7 and runs in ~30-60s.

Both PDFs are image-based (no text layer), so we rasterize each page to PNG
via `pdf_utils.rasterize_pdf` and pass the PNG paths to Claude vision via
`@`-reference (which works without Poppler installed).
"""

import tempfile
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from angel_memos.claude_cli import OPUS_MODEL, extract_structured
from angel_memos.models import AngelListMetadata, Stage
from angel_memos.pdf_utils import rasterize_pdf


class _Terms(BaseModel):
    """Sub-schema for the terms-extraction pass. Mirrors AngelListMetadata
    minus `founders` (which the second pass extracts from the deck)."""

    model_config = ConfigDict(extra="forbid")

    company: str = Field(min_length=1)
    round_label: str = Field(min_length=1)
    stage: Stage
    instrument: str = Field(min_length=1)
    estimated_round_size_usd: float = Field(ge=0.0)
    share_class: str = Field(min_length=1)
    pre_money_usd: float = Field(gt=0.0)
    allocation_usd: float = Field(ge=0.0)
    estimated_expenses_pct: float = Field(ge=0.0, le=1.0)
    leads_investment_usd: float = Field(ge=0.0)
    gross_carry_pct: float = Field(ge=0.0, le=1.0)
    min_investment_usd: float = Field(ge=0.0)
    deadline: date | None = None
    markets: list[str] = Field(min_length=1)
    co_investors: list[str] = []
    total_prior_capital_usd: float | None = Field(default=None, ge=0.0)


class _Founders(BaseModel):
    """Sub-schema for the founders-extraction pass. Empty list is acceptable
    for deals with no deck and no team block in the AL memo — downstream
    diligence will flag it as a gap and search publicly. We just don't want
    placeholder strings like 'Unknown' polluting the data."""

    model_config = ConfigDict(extra="forbid")

    founders: list[str] = []


_TERMS_SYSTEM_PROMPT = """You extract structured AngelList deal terms by
reading the attached PNG images of an AngelList memo PDF.

Read every attached image. The TERMS table is on pages 1-2; co-investors
typically have their own section near the end; "Past financing" appears
near the end too.

Conversion rules:
- Convert percentages to decimals: "11.1%" -> 0.111, "20.0%" -> 0.20
- Dollar amounts are USD; preserve absolute values (not millions or shorthand)
- Markets field may be slash-separated ("AI / ML") — split into a list
- round_label: keep the verbatim string from the TERMS table (e.g.,
  "Series B+", "Seed", "Series A+")
- stage: map round_label to the canonical enum value
  ("Seed" -> seed, "Pre-Seed" -> pre_seed, "Series A" / "Series A+" -> series_a,
   "Series B" / "Series B+" -> series_b, "Series C" / "Series C+" -> series_c,
   anything past Series C -> growth)
- deadline: ISO date if present; otherwise null
- total_prior_capital_usd: only if a specific dollar amount is stated; if
  the memo just says "previously raised a pre-seed round" without a dollar
  figure, use null"""

_FOUNDERS_SYSTEM_PROMPT = """You extract founder names from the attached
team slide of a pitch deck (or, if no deck, the team block of an AngelList
memo). The team slide typically shows headshots in a grid with name + title
beneath each.

Rules:
- Include only people whose title says Co-Founder, Founder, or CEO/CTO/COO/
  CPO/CRO/Chief Architect/President when they are also clearly founders
  (e.g., the slide's section is titled "Founders" or "Founding Team")
- EXCLUDE advisors, investors, board members, and non-founder executives
  even if they appear on the same slide
- Read names from the images carefully — proper spelling matters

If you cannot find any founder names, return an empty list — do NOT emit
placeholder strings like "Unknown" or "TBD"."""


def parse_angellist_metadata(al_pdf: Path, deck_pdf: Path | None = None) -> AngelListMetadata:
    """Read the AL memo PDF (and optionally the pitch deck) via Claude vision
    and return typed metadata.

    Runs two focused extraction passes:
      1. Terms — from the AL memo only.
      2. Founders — from the deck team slide if available, else from the AL
         memo's team block.

    Raises:
      FileNotFoundError: `al_pdf` or `deck_pdf` does not point to a real file.
      pydantic.ValidationError: Claude returned JSON that does not satisfy
        the schema (e.g., percentages emitted as 11.1 instead of 0.111, or a
        Round label that doesn't map to a Stage, or no founder names found).
    """
    if not al_pdf.is_file():
        raise FileNotFoundError(f"AngelList PDF not found: {al_pdf}")
    if deck_pdf is not None and not deck_pdf.is_file():
        raise FileNotFoundError(f"Deck PDF not found: {deck_pdf}")

    with tempfile.TemporaryDirectory(prefix="angel_memos_al_") as tmp_str:
        tmp_dir = Path(tmp_str)
        al_pages = rasterize_pdf(al_pdf, tmp_dir / "al")
        deck_pages: list[Path] = []
        if deck_pdf is not None:
            deck_pages = rasterize_pdf(deck_pdf, tmp_dir / "deck")

        terms = _extract_terms(al_pages)
        founders_pages = deck_pages if deck_pages else al_pages
        founders = _extract_founders(founders_pages)

        combined = {**terms.model_dump(mode="json"), "founders": founders.founders}
        return AngelListMetadata.model_validate(combined)


def _extract_terms(al_pages: list[Path]) -> _Terms:
    refs = "\n".join(f"@{p.as_posix()}" for p in al_pages)
    parent = str(al_pages[0].parent.parent)  # the rasterizer's temp root
    prompt = (
        f"{refs}\n\n"
        "These are pages of an AngelList memo PDF, in order. Extract the "
        "structured deal terms from the TERMS table (pages 1-2), the co-"
        "investors list, the markets tags, and total prior capital from the "
        "'previously raised' note. Return JSON matching the schema."
    )
    return extract_structured(
        prompt,
        _Terms,
        model=OPUS_MODEL,
        system_prompt=_TERMS_SYSTEM_PROMPT,
        additional_dirs=[parent],
    )


def _extract_founders(pages: list[Path]) -> _Founders:
    refs = "\n".join(f"@{p.as_posix()}" for p in pages)
    parent = str(pages[0].parent.parent)
    prompt = (
        f"{refs}\n\n"
        "These are pages of a pitch deck (or AngelList memo if no deck is "
        "available), in order. Find the team slide / team section — it shows "
        "headshots with name + title beneath each. List every FOUNDER (Co-"
        "Founder / Founder / CEO/CTO/COO/CPO/CRO if also a founder). Read the "
        "names carefully from the images. Return JSON matching the schema."
    )
    return extract_structured(
        prompt,
        _Founders,
        model=OPUS_MODEL,
        system_prompt=_FOUNDERS_SYSTEM_PROMPT,
        additional_dirs=[parent],
    )
