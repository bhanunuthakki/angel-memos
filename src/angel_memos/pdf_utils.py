"""PDF rasterization for the Claude vision pipeline.

AngelList memos and pitch decks are image-based PDFs (no text layer). Claude
Code's Read tool relies on Poppler (`pdftoppm`) for PDF rendering; rather
than impose that system-level dep, we rasterize PDFs to PNGs in Python via
pypdfium2 (which bundles its own native renderer) and pass the PNGs to
Claude via @-reference (which works without Poppler).
"""

# pypdfium2's page/bitmap objects surface as unknowns through its partial
# typing; contain the suppression to this thin wrapper module.
# pyright: reportUnknownVariableType=false

from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

# 150 DPI is the sweet spot for vision quality vs. file size: text-heavy
# AL pages are still cleanly OCR-able and a 14-page memo fits in ~5 MB.
DEFAULT_DPI = 150


def rasterize_pdf(pdf_path: Path, out_dir: Path, dpi: int = DEFAULT_DPI) -> list[Path]:
    """Render each page of `pdf_path` as a PNG into `out_dir`. Returns the
    ordered list of PNG paths (sorted by page number).

    Filenames are zero-padded so directory iteration preserves page order.

    Raises:
      FileNotFoundError: `pdf_path` does not exist.
    """
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    out_dir.mkdir(parents=True, exist_ok=True)
    pdf = pdfium.PdfDocument(str(pdf_path))
    scale = dpi / 72.0
    width = max(3, len(str(len(pdf))))

    paths: list[Path] = []
    for i, page in enumerate(pdf):
        # pypdfium2's stub annotates `scale` as int, but the renderer
        # accepts (and the library docs use) float scale factors.
        bitmap = page.render(scale=scale)  # pyright: ignore[reportArgumentType]
        image: Image.Image = bitmap.to_pil()
        out_path = out_dir / f"page_{i + 1:0{width}d}.png"
        image.save(out_path)
        paths.append(out_path)
    return paths
