"""Company-folder loader.

A company folder contains an AngelList memo PDF (required), an optional
pitch deck PDF, free-form note files (.md/.txt), and any other artifacts.
On Phase B re-runs the folder also contains tool-generated outputs from
prior runs; those are skipped so they don't get re-fed as inputs.

Note on PDF text extraction: AL memos and pitch decks downloaded from
AngelList are typically image-based PDFs (rendered, not native-text), so
pypdf yields empty strings on them. That is expected — the AngelList
parser and memo generator pass the PDF file path to Claude (multimodal)
rather than relying on pypdf text. `text` is best-effort and may be empty.
"""

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict
from pypdf import PdfReader

from angel_memos.models import AngelListMetadata, DeckContent

type FileKind = Literal["angellist", "deck", "note", "other"]


# Outputs written by `diligence`, `memo`, and `/angel-decide`. Excluded
# from material loading so re-runs don't reload them as inputs.
OUTPUT_FILENAMES: frozenset[str] = frozenset(
    {
        "diligence_topics.html",
        "diligence_topics.md",  # legacy — older runs may have left this around
        "decision.md",
        "decision_review.md",
        "memo_private.md",
        "memo_public.md",
        "exit_math.xlsx",
        "score_report.json",
        "score_report.md",
        "research_memo.md",
        "job.json",  # extension drop marker — should be consumed by ingest, but be safe
        ".angellist_cache.json",
        ".deck_content_cache.json",
        ".founder_profiles_cache.json",
        ".comparable_deals_cache.json",
        "private_entry.json",
        "public_entry.json",
    }
)

# Matches AngelList memo filenames. AL's natural naming pattern is
# `<Company> AL Details.pdf`; downloads vary, so accept several variants:
#   "angellist" / "angel list" / "angel_list" / "angel-list"
#   "AL" as a standalone token: " al ", "_al_", "-al-", " al.pdf", "_al.pdf", etc.
_ANGELLIST_PATTERN: re.Pattern[str] = re.compile(
    r"angellist|angel[\s_\-]+list|(?:\A|[\s_\-])al(?:[\s_\-]|\.pdf\Z)",
    re.IGNORECASE,
)

_DECK_TOKENS: frozenset[str] = frozenset({"deck", "pitch"})
_TEXT_SUFFIXES: frozenset[str] = frozenset({".md", ".txt"})


class MaterialsError(ValueError):
    """Raised when a company folder violates the expected contract."""


class FileEntry(BaseModel):
    """One classified file from a company folder. Path-only — text extraction
    is lazy via `read_text(entry)` so the loader stays a fast classification
    step (AL memos are large image-PDFs; eager pypdf parsing costs seconds
    per file and adds up in tests/CI)."""

    model_config = ConfigDict(frozen=True)

    path: Path
    kind: FileKind


class Materials(BaseModel):
    """The classified contents of a company folder."""

    model_config = ConfigDict(frozen=True)

    folder: Path
    angellist: FileEntry  # required — exactly one
    deck: FileEntry | None
    notes: list[FileEntry] = []
    other: list[FileEntry] = []


def load_materials(folder: Path) -> Materials:
    """Walk `folder` (non-recursive), classify each file, and return a
    `Materials` instance. Raises `MaterialsError` if the folder is missing,
    contains no AngelList memo, or contains multiple AL memos."""
    if not folder.is_dir():
        raise MaterialsError(f"folder does not exist or is not a directory: {folder}")

    angellists: list[FileEntry] = []
    decks: list[FileEntry] = []
    notes: list[FileEntry] = []
    other: list[FileEntry] = []

    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.name in OUTPUT_FILENAMES:
            continue
        kind = _classify(path)
        entry = FileEntry(path=path, kind=kind)
        match kind:
            case "angellist":
                angellists.append(entry)
            case "deck":
                decks.append(entry)
            case "note":
                notes.append(entry)
            case "other":
                other.append(entry)

    if not angellists:
        raise MaterialsError(f"no angellist memo found in {folder}")
    if len(angellists) > 1:
        names = ", ".join(a.path.name for a in angellists)
        raise MaterialsError(f"multiple angellist memos in {folder}: {names}")

    return Materials(
        folder=folder,
        angellist=angellists[0],
        deck=decks[0] if decks else None,
        notes=notes,
        other=other,
    )


def read_text(entry: FileEntry) -> str:
    """Extract text content. PDFs via pypdf (best-effort — image-only PDFs
    return empty string; pass to Claude vision instead). Markdown/text files
    read directly. Other binaries return empty string."""
    suffix = entry.path.suffix.lower()
    if suffix == ".pdf":
        reader = PdfReader(str(entry.path))
        pages: list[str] = []
        for page in reader.pages:
            extracted = page.extract_text()
            if extracted:
                pages.append(extracted)
        return "\n\n".join(pages)
    if suffix in _TEXT_SUFFIXES:
        return entry.path.read_text(encoding="utf-8")
    return ""


# ---------------------------------------------------------------------------
# Cached parses of AL memo + deck — used by both diligence and memo flows.
# The parse calls themselves are heavy (Claude vision); cache to avoid
# redundant work. Invalidate by deleting the cache file.
# ---------------------------------------------------------------------------

_ANGELLIST_CACHE_FILENAME = ".angellist_cache.json"
_DECK_CACHE_FILENAME = ".deck_content_cache.json"


def load_or_parse_angellist(folder: Path, materials: Materials) -> AngelListMetadata:
    """Cached AngelListMetadata. Invalidated by deleting the cache file."""
    # Imported lazily — angellist.py depends on claude_cli which is heavy
    # to import for callers that only need text-loading helpers.
    from angel_memos.angellist import parse_angellist_metadata

    cache_path = folder / _ANGELLIST_CACHE_FILENAME
    if cache_path.is_file():
        return AngelListMetadata.model_validate_json(cache_path.read_text(encoding="utf-8"))
    deck_path = materials.deck.path if materials.deck is not None else None
    result = parse_angellist_metadata(materials.angellist.path, deck_pdf=deck_path)
    cache_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return result


def load_or_parse_deck(folder: Path, materials: Materials) -> DeckContent | None:
    """Cached `DeckContent` if a deck exists in the folder; otherwise None.
    Invalidated by deleting the cache file."""
    if materials.deck is None:
        return None
    from angel_memos.deck import parse_deck_content

    cache_path = folder / _DECK_CACHE_FILENAME
    if cache_path.is_file():
        return DeckContent.model_validate_json(cache_path.read_text(encoding="utf-8"))
    result = parse_deck_content(materials.deck.path)
    cache_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return result


def _classify(path: Path) -> FileKind:
    lower = path.name.lower()
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        if _ANGELLIST_PATTERN.search(lower):
            return "angellist"
        if any(token in lower for token in _DECK_TOKENS):
            return "deck"
    if suffix in _TEXT_SUFFIXES:
        return "note"
    return "other"
