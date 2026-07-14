"""Folder loader for company materials. Classifies files into angellist /
deck / note / other; extracts text from PDFs and Markdown/text files;
skips tool-generated outputs."""

from pathlib import Path

import pytest

from pydantic import BaseModel

from angel_memos.materials import (
    OUTPUT_FILENAMES,
    FileEntry,
    MaterialsError,
    _read_fingerprinted_cache,
    _source_fingerprint,
    _write_fingerprinted_cache,
    load_materials,
    read_text,
)


class _Toy(BaseModel):
    value: str

FIXTURES = Path(__file__).parent / "fixtures"
SPOTAI_AL_PDF = FIXTURES / "spotai_al.pdf"
SPOTAI_DECK_PDF = FIXTURES / "spotai_deck.pdf"


def _copy_fixture(src: Path, dst: Path) -> None:
    dst.write_bytes(src.read_bytes())


def _stub_pdf(path: Path) -> None:
    """Minimal valid PDF for classification tests that don't exercise content
    extraction. Avoids the cost of re-parsing the 4.6MB SpotAI fixture per
    test (image-heavy PDFs are slow even when pypdf yields empty text)."""
    path.write_bytes(b"%PDF-1.4\n%stub\n")


def test_load_materials_picks_up_angellist(tmp_path: Path) -> None:
    _stub_pdf(tmp_path / "spotai_al.pdf")
    m = load_materials(tmp_path)
    assert m.angellist.path.name == "spotai_al.pdf"
    assert m.angellist.kind == "angellist"
    assert m.deck is None
    assert m.notes == []
    assert m.other == []


def test_load_materials_picks_up_deck(tmp_path: Path) -> None:
    _stub_pdf(tmp_path / "angellist.pdf")
    _stub_pdf(tmp_path / "pitch_deck.pdf")
    m = load_materials(tmp_path)
    assert m.deck is not None
    assert m.deck.kind == "deck"


def test_load_materials_classifies_notes(tmp_path: Path) -> None:
    _stub_pdf(tmp_path / "angellist.pdf")
    (tmp_path / "founder_call_notes.md").write_text("# Call notes\nLong-time CEO.")
    (tmp_path / "links.txt").write_text("https://example.com/article")
    m = load_materials(tmp_path)
    note_names = sorted(n.path.name for n in m.notes)
    assert note_names == ["founder_call_notes.md", "links.txt"]
    assert all(n.kind == "note" for n in m.notes)


def test_read_text_returns_str_for_pdf(tmp_path: Path) -> None:
    """pypdf returns whatever text layer the PDF has. AL memos from AngelList
    are image-based PDFs (the typical case) — empty string. Test asserts
    the contract is a str; the AngelList parser uses Claude vision on the
    file path regardless of pypdf output."""
    _copy_fixture(SPOTAI_AL_PDF, tmp_path / "angellist.pdf")
    m = load_materials(tmp_path)
    assert isinstance(read_text(m.angellist), str)


def test_read_text_reads_markdown_directly(tmp_path: Path) -> None:
    _stub_pdf(tmp_path / "angellist.pdf")
    content = "# Founder call\nKey takeaway: strong commercial chops."
    (tmp_path / "founder_call.md").write_text(content, encoding="utf-8")
    m = load_materials(tmp_path)
    assert read_text(m.notes[0]) == content


def test_load_materials_skips_output_files(tmp_path: Path) -> None:
    _stub_pdf(tmp_path / "angellist.pdf")
    # Outputs from prior runs of this tool should not be reloaded as inputs.
    for name in OUTPUT_FILENAMES:
        (tmp_path / name).write_text("output content")
    m = load_materials(tmp_path)
    assert m.notes == []
    assert m.other == []


def test_load_materials_rejects_missing_angellist(tmp_path: Path) -> None:
    _stub_pdf(tmp_path / "deck.pdf")
    with pytest.raises(MaterialsError, match="angellist"):
        load_materials(tmp_path)


def test_load_materials_prefers_newest_angellist_over_failing(tmp_path: Path) -> None:
    """A re-capture leaves a second angellist*.pdf. Rather than hard-fail every
    downstream phase (the old behavior), load_materials picks the newest."""
    import os
    import time

    old = tmp_path / "angellist_a.pdf"
    new = tmp_path / "angellist_b.pdf"
    _stub_pdf(old)
    _stub_pdf(new)
    # Make 'new' unambiguously newer regardless of filesystem mtime ordering.
    past = time.time() - 1000
    os.utime(old, (past, past))

    m = load_materials(tmp_path)  # no raise
    assert m.angellist.path.name == "angellist_b.pdf"


def test_load_materials_rejects_missing_folder(tmp_path: Path) -> None:
    missing = tmp_path / "no_such"
    with pytest.raises(MaterialsError, match="folder"):
        load_materials(missing)


def test_load_materials_classifies_spotai_naming_pattern(tmp_path: Path) -> None:
    """AngelList's natural filename pattern includes 'AL' as a separate token
    (e.g. 'SpotAI AL Details.pdf'); the classifier accepts this without
    requiring rename to 'angellist.pdf'."""
    _stub_pdf(tmp_path / "SpotAI AL Details.pdf")
    m = load_materials(tmp_path)
    assert m.angellist.kind == "angellist"
    assert m.angellist.path.name == "SpotAI AL Details.pdf"


# ---------------------------------------------------------------------------
# Fingerprinted parse caches (#5/#7): invalidate when source files change.
# ---------------------------------------------------------------------------


def _entry(path: Path) -> FileEntry:
    _stub_pdf(path)
    return FileEntry(path=path, kind="angellist")


def test_cache_roundtrips_when_fingerprint_matches(tmp_path: Path) -> None:
    cache = tmp_path / ".cache.json"
    al = _entry(tmp_path / "al.pdf")
    fp = _source_fingerprint(al, None)
    _write_fingerprinted_cache(cache, _Toy(value="hi"), fp)
    got = _read_fingerprinted_cache(cache, _Toy, fp, legacy_ok=False)
    assert got is not None and got.value == "hi"


def test_cache_invalidates_when_deck_appears(tmp_path: Path) -> None:
    """AL cache written with no deck must be discarded once a deck exists."""
    cache = tmp_path / ".cache.json"
    al = _entry(tmp_path / "al.pdf")
    _write_fingerprinted_cache(cache, _Toy(value="hi"), _source_fingerprint(al, None))
    deck = _entry(tmp_path / "deck.pdf")
    fp_with_deck = _source_fingerprint(al, deck)
    assert _read_fingerprinted_cache(cache, _Toy, fp_with_deck, legacy_ok=False) is None


def test_cache_invalidates_when_source_size_changes(tmp_path: Path) -> None:
    cache = tmp_path / ".cache.json"
    al = _entry(tmp_path / "al.pdf")
    _write_fingerprinted_cache(cache, _Toy(value="hi"), _source_fingerprint(al, None))
    (tmp_path / "al.pdf").write_bytes(b"%PDF-1.4\n%much bigger content now\n")
    new_fp = _source_fingerprint(FileEntry(path=tmp_path / "al.pdf", kind="angellist"), None)
    assert _read_fingerprinted_cache(cache, _Toy, new_fp, legacy_ok=False) is None


def test_legacy_bare_cache_accepted_only_when_allowed(tmp_path: Path) -> None:
    cache = tmp_path / ".cache.json"
    cache.write_text(_Toy(value="legacy").model_dump_json(), encoding="utf-8")
    al = _entry(tmp_path / "al.pdf")
    fp = _source_fingerprint(al, None)
    # legacy_ok=False (a deck is present now) -> re-parse
    assert _read_fingerprinted_cache(cache, _Toy, fp, legacy_ok=False) is None
    # legacy_ok=True (no deck) -> keep the legacy cache
    kept = _read_fingerprinted_cache(cache, _Toy, fp, legacy_ok=True)
    assert kept is not None and kept.value == "legacy"
