"""AngelList memo parser.

Extracts `AngelListMetadata` via focused Claude calls:

  1. Terms extraction (vision) — reads the AL memo pages and pulls structured
     deal terms (round, valuation, fees, carry, allocation, markets, co-
     investors, prior capital).
  2. Founders extraction — tried in reliability order: (a) the pitch deck
     team slide via vision when a deck is present, then (b) the AL memo TEXT
     layer when the memo names founders in prose (the reliable path), then
     (c) vision over the AL memo images for image-only memos. The AL memo is
     required and always present, so it is the reliable backstop.

Why the fallback: a file classified as a "deck" is not always a real pitch
deck with a headshot team slide. AngelList SPV attachments are frequently
investor updates or teasers with no team block. Reading only such a deck
returns `founders: []` even when the AL memo body plainly names the founder
(e.g. "<Name> is now the CEO of <prior company>"). Empty founders then floors
the scoring team factor to its "no pedigree surfaced" minimum, so recovering
them from the memo body is load-bearing.

Why two passes rather than one big call: empirically, with 30+ attached
images Claude skims rather than reading deeply, and produces placeholder
values like `founders: ["Unknown"]`. Splitting into focused calls (each
seeing ~15 images) restores reliable extraction. Each call costs ~$0.10
on Opus 4.7 and runs in ~30-60s.

Image-based AL memos / decks (older scans, some captures) have no text
layer, so we rasterize each page to PNG via `pdf_utils.rasterize_pdf` and
pass the PNG paths to Claude vision via `@`-reference (which works without
Poppler installed). BUT AngelList memos captured by the Chrome extension's
print-to-PDF keep a real text layer, and founders are often named only in
the memo's prose body (not a headshot grid). Vision skims prose-buried names
unreliably, so when a text layer is present we extract founders from the TEXT
first — that is the reliable path that stops `founders` from silently
emptying and flooring the scoring team factor.
"""

import logging
import re
import tempfile
from datetime import date
from pathlib import Path
from typing import Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field

from angel_memos.claude import Purpose, extract_structured
from angel_memos.models import AngelListMetadata, Stage
from angel_memos.pdf_utils import rasterize_pdf, read_pdf_text

logger = logging.getLogger(__name__)

# An AL memo with at least this many extractable characters is treated as
# having a usable text layer; below it we fall back to vision on the images.
_MIN_TEXT_LAYER_CHARS = 200


class _Terms(BaseModel):
    """Sub-schema for the terms-extraction pass. Mirrors AngelListMetadata
    minus `founders` (which the separate founders pass extracts)."""

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

_FOUNDERS_SYSTEM_PROMPT = """You extract founder names from the provided
material — pitch-deck / memo images, or the extracted text of an AngelList
memo. The founders may be presented in either of two ways:

1. A pitch-deck team slide — headshots in a grid with name + title beneath
   each, under a heading like "Team", "Founders", or "Founding Team".
2. An AngelList memo body — the founders are named in PROSE inside the
   narrative "Memo" / deal-writeup section, not a headshot grid. Look for
   sentences and blurbs such as "<Name> is the CEO", "founded by <Name>",
   "the founding team is <Name> and <Name>", "co-founder <Name>", or a
   biographical sentence naming the founder and their prior company
   (including quoted press excerpts). Read the memo body carefully — this is
   the most common place a founder hides.

Rules:
- Include only people who are FOUNDERS: title says Co-Founder / Founder, or
  CEO/CTO/COO/CPO/CRO/Chief Architect/President when the text also makes
  clear they founded the company.
- EXCLUDE advisors, investors, board members, the AngelList deal partner /
  syndicate lead, and non-founder executives, even if named nearby.
- Read names carefully from the images — proper spelling matters.

If you genuinely cannot find any founder names, return an empty list — do
NOT emit placeholder strings like "Unknown" or "TBD"."""


def parse_angellist_metadata(al_pdf: Path, deck_pdf: Path | None = None) -> AngelListMetadata:
    """Read the AL memo PDF (and optionally the pitch deck) via Claude vision
    and return typed metadata.

    Runs two focused extraction passes:
      1. Terms — from the AL memo only.
      2. Founders — from the deck team slide if it yields any; otherwise (no
         deck, or a deck with no team slide) from the AL memo body.

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
        al_text = read_pdf_text(al_pdf)
        founders = _extract_founders(al_text, al_pages, deck_pages)

        combined = {**terms.model_dump(mode="json"), "founders": founders.founders}
        return AngelListMetadata.model_validate(combined)


class RoundId(NamedTuple):
    """A deal's round, as both the canonical stage and the memo's own wording.

    `stage` is what two captures are compared on (so "Series C" and "Series C+"
    are one round); `label` is the verbatim TERMS string, used where a human
    reads it — e.g. an ingest folder suffix."""

    stage: Stage
    label: str


# Ordered longest-prefix-first: "pre-seed" must beat "seed", and an explicit
# "series d"+ maps to growth since the Stage enum stops at series_c.
_STAGE_PREFIXES: tuple[tuple[re.Pattern[str], Stage], ...] = (
    (re.compile(r"^pre[-\s]?seed"), Stage.PRE_SEED),
    (re.compile(r"^seed"), Stage.SEED),
    (re.compile(r"^series\s*a"), Stage.SERIES_A),
    (re.compile(r"^series\s*b"), Stage.SERIES_B),
    (re.compile(r"^series\s*c"), Stage.SERIES_C),
    (re.compile(r"^(series\s*[d-h]|growth|late[-\s]?stage)"), Stage.GROWTH),
)

# The TERMS table's Round row, e.g. "Round Series B+". Anchored to a line start
# so prose ("...entry point. Round was priced earlier this year.") can't match.
_ROUND_ROW = re.compile(r"(?im)^\s*Round[ \t]+(?P<label>\S[^\n]*?)\s*$")

# How far past the TERMS heading the Round row may sit. The table is a short
# key/value block; a wider window would start catching narrative prose.
_TERMS_WINDOW_CHARS = 1500


def parse_round(memo_text: str) -> RoundId | None:
    """The round from an AL memo's extracted text, or None if not determinable.

    Deliberately conservative: it reads only the Round row of the TERMS table
    and only accepts a label that maps to a known `Stage`. Anything else —
    no TERMS block (some captures render it as an image), an unrecognised
    label — returns None so callers treat the round as unknown rather than
    acting on a guess."""
    terms = re.search(r"\bTERMS\b", memo_text)
    if terms is None:
        return None
    window = memo_text[terms.start() : terms.start() + _TERMS_WINDOW_CHARS]
    row = _ROUND_ROW.search(window)
    if row is None:
        return None
    label = row.group("label").strip()
    normalized = label.casefold()
    for pattern, stage in _STAGE_PREFIXES:
        if pattern.search(normalized):
            return RoundId(stage=stage, label=label)
    return None


def read_round(pdf_path: Path) -> RoundId | None:
    """The round from an AL memo PDF's text layer, or None when unavailable
    (image-only capture, unparsable file, or no TERMS table)."""
    return parse_round(read_pdf_text(pdf_path))


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
        purpose=Purpose.ANGELLIST_TERMS,
        system_prompt=_TERMS_SYSTEM_PROMPT,
        additional_dirs=[parent],
        image_paths=al_pages,
    )


def _extract_founders(al_text: str, al_pages: list[Path], deck_pages: list[Path]) -> _Founders:
    """Extract founders, trying sources in reliability order.

    A "deck" file isn't always a real pitch deck with a team slide (it may be
    an investor update or teaser), and founders are frequently named only in
    the AL memo's prose body — which vision skims unreliably. So:

      1. Deck team slide (vision) — most complete when the deck is a real deck.
      2. AL memo TEXT layer — reliable for prose-named founders; only used when
         the memo actually has a text layer (Chrome-print AL captures do).
      3. AL memo images (vision) — backstop for image-only memos.

    Each step is skipped if it can't apply and short-circuits on the first
    non-empty result. This chain is what keeps `founders` from silently
    emptying and flooring the scoring team factor."""
    if deck_pages:
        from_deck = _extract_founders_from_images(deck_pages, source="deck")
        if from_deck.founders:
            return from_deck
    if len(al_text.strip()) >= _MIN_TEXT_LAYER_CHARS:
        from_text = _extract_founders_from_text(al_text)
        if from_text.founders:
            return from_text
    return _extract_founders_from_images(al_pages, source="memo")


def _extract_founders_from_text(memo_text: str) -> _Founders:
    """Extract founders from the AL memo's extracted text. Reliable for
    founders named in prose ('<Name> is the CEO of <prior company>') that
    vision tends to skim past."""
    prompt = (
        "Below is the extracted TEXT of an AngelList memo. Founders are usually "
        "named in the narrative body as prose — e.g. '<Name> is the CEO', "
        "'founded by <Name>', or a biographical line naming the founder and "
        "their prior company (including quoted press excerpts). List every "
        "FOUNDER (Co-Founder / Founder / CEO/CTO/COO/CPO/CRO if also a "
        "founder). Return JSON matching the schema.\n\n"
        "=== ANGELLIST MEMO TEXT ===\n"
        f"{memo_text}"
    )
    return extract_structured(
        prompt,
        _Founders,
        purpose=Purpose.ANGELLIST_FOUNDERS,
        system_prompt=_FOUNDERS_SYSTEM_PROMPT,
    )


def _extract_founders_from_images(
    pages: list[Path], *, source: Literal["deck", "memo"]
) -> _Founders:
    refs = "\n".join(f"@{p.as_posix()}" for p in pages)
    parent = str(pages[0].parent.parent)
    if source == "deck":
        where = (
            "These are pages of a pitch deck, in order. Find the team slide / "
            "team section — headshots with name + title beneath each."
        )
    else:
        where = (
            "These are pages of an AngelList memo, in order. The founders are "
            "usually named in PROSE in the narrative 'Memo' / deal-writeup "
            "section, not a headshot grid — look for sentences and press "
            "blurbs like '<Name> is the CEO', 'founded by <Name>', or a "
            "biographical line naming the founder and their prior company. "
            "Read the memo body carefully."
        )
    prompt = (
        f"{refs}\n\n"
        f"{where} List every FOUNDER (Co-Founder / Founder / "
        "CEO/CTO/COO/CPO/CRO if also a founder). Read the names carefully "
        "from the images. Return JSON matching the schema."
    )
    return extract_structured(
        prompt,
        _Founders,
        purpose=Purpose.ANGELLIST_FOUNDERS,
        system_prompt=_FOUNDERS_SYSTEM_PROMPT,
        additional_dirs=[parent],
        image_paths=pages,
    )
