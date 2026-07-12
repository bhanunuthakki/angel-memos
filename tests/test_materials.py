"""Folder loader for company materials. Classifies files into angellist /
deck / note / other; extracts text from PDFs and Markdown/text files;
skips tool-generated outputs."""

from pathlib import Path

import pytest

from angel_memos.materials import (
    OUTPUT_FILENAMES,
    MaterialsError,
    load_materials,
    read_text,
)

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


def test_load_materials_rejects_multiple_angellist(tmp_path: Path) -> None:
    _stub_pdf(tmp_path / "angellist_a.pdf")
    _stub_pdf(tmp_path / "angellist_b.pdf")
    with pytest.raises(MaterialsError, match="multiple"):
        load_materials(tmp_path)


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
