"""PDF rasterization tests. One slow test against the real SpotAI fixture
verifies pypdfium2 actually produces PNGs; the rest exercise edge cases
against synthetic inputs."""

from pathlib import Path

import pytest

from angel_memos.pdf_utils import rasterize_pdf

FIXTURES = Path(__file__).parent / "fixtures"
SPOTAI_AL_PDF = FIXTURES / "spotai_al.pdf"


def test_rasterize_produces_one_png_per_page(tmp_path: Path) -> None:
    out_dir = tmp_path / "pngs"
    paths = rasterize_pdf(SPOTAI_AL_PDF, out_dir)
    assert len(paths) == 14  # SpotAI AL is 14 pages
    assert all(p.exists() for p in paths)
    assert all(p.suffix == ".png" for p in paths)
    assert all(p.parent == out_dir for p in paths)


def test_rasterize_orders_pages_for_filesystem_sort(tmp_path: Path) -> None:
    """Filenames must zero-pad so `sorted(folder.iterdir())` preserves page order."""
    out_dir = tmp_path / "pngs"
    paths = rasterize_pdf(SPOTAI_AL_PDF, out_dir)
    sorted_names = sorted(p.name for p in paths)
    assert [p.name for p in paths] == sorted_names


def test_rasterize_creates_output_directory(tmp_path: Path) -> None:
    out_dir = tmp_path / "deep" / "nested" / "pngs"
    paths = rasterize_pdf(SPOTAI_AL_PDF, out_dir)
    assert out_dir.is_dir()
    assert len(paths) > 0


def test_rasterize_rejects_missing_pdf(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        rasterize_pdf(tmp_path / "nope.pdf", tmp_path / "out")
