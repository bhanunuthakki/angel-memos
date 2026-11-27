"""PyMuPDF-backed PDF text extraction and page rasterization.

Synced with earnings-summary's canonical PyMuPDF patterns
(`src/runtime/pdf_text.py` and `src/pipeline/pdf_worker.py`) so both text
extraction and PNG rendering share one dependency without pypdf/pypdfium2/PIL.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Protocol, cast

import pymupdf

logger = logging.getLogger(__name__)

# 150 DPI balances vision readability and payload size across multi-page memos.
DEFAULT_DPI = 150
MAX_PDF_RENDER_PIXELS = 16_000_000
MAX_PDF_RENDER_DIMENSION = 8192


class _PdfRect(Protocol):
    width: float
    height: float


class _PdfPixmap(Protocol):
    def save(self, filename: str) -> None: ...


class _PdfPage(Protocol):
    @property
    def rect(self) -> _PdfRect: ...

    def get_text(self) -> object: ...

    def get_pixmap(self, *, dpi: int) -> _PdfPixmap: ...


class _PdfDocument(Protocol):
    page_count: int

    def load_page(self, page_id: int) -> _PdfPage: ...

    def close(self) -> None: ...


def _open_pdf(pdf_path: Path) -> _PdfDocument:
    return cast("_PdfDocument", pymupdf.open(str(pdf_path)))


def read_pdf_text(pdf_path: Path, *, max_pages: int | None = None) -> str:
    """Best-effort text-layer extraction via PyMuPDF.

    Returns "" for image-only PDFs or unreadable files so callers can fall back
    to multimodal vision extraction.
    """
    try:
        doc = _open_pdf(pdf_path)
    except Exception as exc:
        logger.warning("could not open %s for text extraction: %s", pdf_path, exc)
        return ""
    try:
        count = doc.page_count if max_pages is None else min(max_pages, doc.page_count)
        pages: list[str] = []
        for idx in range(count):
            text = doc.load_page(idx).get_text()
            pages.append(text if isinstance(text, str) else "")
        return "\n\n".join(pages)
    except Exception as exc:
        logger.warning("could not read text layer of %s: %s", pdf_path, exc)
        return ""
    finally:
        doc.close()


def rasterize_pdf(pdf_path: Path, out_dir: Path, dpi: int = DEFAULT_DPI) -> list[Path]:
    """Render each page of `pdf_path` as a PNG into `out_dir` via PyMuPDF.

    Filenames are zero-padded so directory iteration preserves page order.

    Raises:
      FileNotFoundError: `pdf_path` does not exist.
      ValueError: a page exceeds the bounded rasterization safety envelope.
    """
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    out_dir.mkdir(parents=True, exist_ok=True)
    doc = _open_pdf(pdf_path)
    try:
        width_pad = max(3, len(str(doc.page_count)))
        paths: list[Path] = []
        for idx in range(doc.page_count):
            page = doc.load_page(idx)
            px_w = page.rect.width * dpi / 72.0
            px_h = page.rect.height * dpi / 72.0
            if (
                not all(math.isfinite(val) and val > 0 for val in (px_w, px_h))
                or max(px_w, px_h) > MAX_PDF_RENDER_DIMENSION
                or math.ceil(px_w) * math.ceil(px_h) > MAX_PDF_RENDER_PIXELS
            ):
                raise ValueError(
                    f"PDF page {idx + 1} exceeds safe rasterization bounds ({px_w:.0f}x{px_h:.0f})"
                )
            out_path = out_dir / f"page_{idx + 1:0{width_pad}d}.png"
            page.get_pixmap(dpi=dpi).save(str(out_path))
            paths.append(out_path)
        return paths
    finally:
        doc.close()
