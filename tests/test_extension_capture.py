"""Static contracts for the unpacked Chrome extension.

The extension has no JavaScript test runner; these tests protect the small
configuration values that materially affect capture reliability.
"""

import json
import re
from pathlib import Path

_CONTENT_SCRIPT = Path(__file__).parents[1] / "extension" / "content.js"
_MANIFEST = Path(__file__).parents[1] / "extension" / "manifest.json"


def test_deck_fetch_waits_up_to_sixty_seconds() -> None:
    source = _CONTENT_SCRIPT.read_text(encoding="utf-8")
    match = re.search(r"const DOC_FETCH_TIMEOUT_MS = (\d+);", source)

    assert match is not None
    assert int(match.group(1)) == 60_000


def test_timeout_fix_bumps_extension_patch_version() -> None:
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))

    assert manifest["version"] == "0.4.2"
