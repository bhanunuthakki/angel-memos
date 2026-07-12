"""Dump a Google Doc's textual content with heading hierarchy preserved.
Used to study existing user-written entries when building style guides."""

import sys
from pathlib import Path

from googleapiclient.discovery import build

# Force UTF-8 stdout so Unicode (em-dashes, arrows) in the docs doesn't crash
# the CP1252 default on Windows.
sys.stdout.reconfigure(encoding="utf-8")

# Lets us import from src/angel_memos without an editable install.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from angel_memos.config import load_config  # noqa: E402
from angel_memos.google_docs import (  # noqa: E402
    _load_credentials,  # type: ignore[reportPrivateUsage]
)


def _style_to_marker(style: str) -> str:
    """Map a HEADING_N style to a Markdown-ish prefix for readability."""
    if not style.startswith("HEADING_"):
        return ""
    level = int(style.removeprefix("HEADING_"))
    return "#" * level + " "


def dump(doc_id: str) -> None:
    service = build("docs", "v1", credentials=_load_credentials())
    doc = service.documents().get(documentId=doc_id).execute()
    print(f"=== Title: {doc.get('title', '<untitled>')} ===")
    for elem in doc.get("body", {}).get("content", []):
        para = elem.get("paragraph")
        if para is None:
            continue
        style = para.get("paragraphStyle", {}).get("namedStyleType", "")
        text = "".join(
            run.get("textRun", {}).get("content", "")
            for run in para.get("elements", [])
            if "textRun" in run
        )
        marker = _style_to_marker(style)
        # Compress empty paragraphs
        if not text.strip() and not marker:
            continue
        # Show bullet hint
        bullet = "* " if para.get("bullet") else ""
        print(f"{marker}{bullet}{text.rstrip()}")


def main() -> None:
    cfg = load_config()
    if len(sys.argv) > 1 and sys.argv[1] == "public":
        dump(cfg.public_doc_id)
    else:
        dump(cfg.private_doc_id)


if __name__ == "__main__":
    main()
