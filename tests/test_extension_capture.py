"""Static contracts for the unpacked Chrome extension.

The extension has no JavaScript test runner; these tests protect the small
configuration values that materially affect capture reliability.
"""

import json
import re
from pathlib import Path

_CONTENT_SCRIPT = Path(__file__).parents[1] / "extension" / "content.js"
_BACKGROUND = Path(__file__).parents[1] / "extension" / "background.js"
_MANIFEST = Path(__file__).parents[1] / "extension" / "manifest.json"


def test_deck_fetch_waits_up_to_sixty_seconds() -> None:
    source = _CONTENT_SCRIPT.read_text(encoding="utf-8")
    match = re.search(r"const DOC_FETCH_TIMEOUT_MS = (\d+);", source)

    assert match is not None
    assert int(match.group(1)) == 60_000


def test_timeout_fix_bumps_extension_patch_version() -> None:
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))

    assert manifest["version"] == "0.4.3"


# ---------------------------------------------------------------------------
# job.json crash-safety.
#
# job.json is the drop-completeness marker, written last. When the MV3 service
# worker was killed mid-capture (it is terminated after ~30s idle, and
# setTimeout does not reset that timer) the in-memory capture state died with
# it: the PDFs were on disk but no job.json ever landed, so `angel-memos
# ingest` skipped the drop forever. These pin the mechanisms that prevent it.
# ---------------------------------------------------------------------------


def test_manifest_grants_storage_and_alarms() -> None:
    """Capture state is mirrored to chrome.storage.session and the finalize
    backstop is an alarm; without both permissions the recovery path is dead
    code that fails at runtime rather than in CI."""
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))

    assert "storage" in manifest["permissions"]
    assert "alarms" in manifest["permissions"]


def test_capture_state_is_persisted_outside_worker_memory() -> None:
    source = _BACKGROUND.read_text(encoding="utf-8")

    assert "chrome.storage.session" in source


def test_finalize_has_an_alarm_backstop() -> None:
    """Alarms wake a terminated service worker; timers do not. The guard is
    what guarantees an abandoned capture still gets its job.json."""
    source = _BACKGROUND.read_text(encoding="utf-8")

    assert "chrome.alarms.onAlarm.addListener" in source
    assert "chrome.alarms.create" in source


def test_guard_delay_outlives_the_normal_capture_window() -> None:
    """The backstop must not fire while a healthy capture is still running —
    it has to clear both MAX_RUN_MS and the content script's deck fetch."""
    source = _BACKGROUND.read_text(encoding="utf-8")
    guard = re.search(r"const GUARD_DELAY_MINUTES = ([\d.]+);", source)
    max_run = re.search(r"const MAX_RUN_MS = (\d+);", source)
    deck_fetch = re.search(
        r"const DOC_FETCH_TIMEOUT_MS = (\d+);", _CONTENT_SCRIPT.read_text(encoding="utf-8")
    )

    assert guard is not None and max_run is not None and deck_fetch is not None
    guard_ms = float(guard.group(1)) * 60_000
    assert guard_ms > int(max_run.group(1))
    assert guard_ms > int(deck_fetch.group(1))


def test_quiescence_reconciles_against_the_download_manager() -> None:
    """A missed onChanged delta used to wedge an id in `pending` forever,
    forcing the full MAX_RUN_MS wait — the window the worker died in."""
    source = _BACKGROUND.read_text(encoding="utf-8")

    assert "chrome.downloads.search" in source


def test_finalize_is_idempotent() -> None:
    """The guard alarm and an am-finalize message can both arrive; job.json
    must be written exactly once."""
    source = _BACKGROUND.read_text(encoding="utf-8")

    assert "active.finalizing" in source
