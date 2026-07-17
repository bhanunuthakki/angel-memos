"""Shared pytest setup.

The real diligence fixtures under `tests/fixtures/` are personal AngelList deal
material and are deliberately gitignored ("Sensitive/personal fixtures — drop
your own copies locally; never commit."). So on a fresh checkout or in CI they
are absent, which used to fail the non-integration PDF tests.

To keep those tests runnable everywhere, synthesize a placeholder `spotai_al.pdf`
with the same page count when no real fixture exists. These tests assert only
structural properties (page count, PNG output, that `read_text` returns a str),
so a blank-page PDF satisfies them fully. A real fixture dropped locally is never
overwritten — the `-m integration` tests that inspect its actual content still
use the genuine file.
"""

from pathlib import Path

import pytest

_FIXTURES = Path(__file__).parent / "fixtures"

# The real SpotAI AL memo is 14 pages; test_pdf_utils asserts this exact count,
# so the placeholder must match it.
_SPOTAI_AL_PAGES = 14


def _write_blank_pdf(path: Path, pages: int) -> None:
    """Write a minimal valid `pages`-page PDF (US Letter) using pypdf."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)  # 8.5x11in in points
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        writer.write(fh)


@pytest.fixture(scope="session", autouse=True)
def _synthesize_missing_fixtures() -> None:
    """Generate placeholder diligence fixtures that aren't present locally."""
    spotai_al = _FIXTURES / "spotai_al.pdf"
    if not spotai_al.exists():
        _write_blank_pdf(spotai_al, _SPOTAI_AL_PAGES)
