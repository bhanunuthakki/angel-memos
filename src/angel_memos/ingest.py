"""Inbox ingestion: move Chrome-extension drops into the Evaluation tree.

The extension can only write under ~/Downloads, so it drops each captured
deal into `Downloads/angel-memos/<Company>/` and writes `job.json` LAST —
after every download completes — as the ready marker. The watcher (or a
manual `angel-memos ingest`) moves the files into
`<Evaluation>/<Company>/` where the normal folder contract applies.

A drop is ready iff `job.json` exists and no in-flight download artifacts
(.crdownload/.tmp/.partial) remain.
"""

from __future__ import annotations

import contextlib
import logging
import re
import shutil
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from angel_memos.config import Config

logger = logging.getLogger(__name__)

JOB_FILENAME = "job.json"

# A quarantined drop keeps its job.json but must never be re-scanned.
FAILED_SUFFIX = ".failed"

_IN_FLIGHT_SUFFIXES: frozenset[str] = frozenset({".crdownload", ".tmp", ".partial"})

# Characters that are illegal in a Windows path component, plus the path
# separators. The extension sanitizes the drop-folder name but writes the RAW
# company string into job.json, so ingest must re-sanitize before using it as a
# directory name — otherwise a name like "Re:Build" or "K/O Ventures" either
# wedges mkdir or escapes the Evaluation root.
_ILLEGAL_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Deck filename hint mirrors materials._DECK_TOKENS without importing it —
# ingest only needs a coarse "did a deck come through" flag.
_DECK_HINT: tuple[str, ...] = ("deck", "pitch")

# Mirrors materials._ANGELLIST_PATTERN's intent without importing the
# private regex: ingest only needs a coarse "did we get an AL memo" flag.
_ANGELLIST_HINT = "angellist"


def default_inbox() -> Path:
    return Path.home() / "Downloads" / "angel-memos"


class JobRequest(BaseModel):
    """What the extension asked for, written as `job.json` in the drop."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    company: str = Field(min_length=1)
    tier: Literal["none", "quick"] = "none"
    source_url: str = ""


class IngestResult(BaseModel):
    """One processed drop."""

    model_config = ConfigDict(frozen=True)

    job: JobRequest
    folder: Path
    moved: list[str]
    missing_angellist: bool
    missing_deck: bool = True


def scan_inbox(inbox: Path) -> list[Path]:
    """Drop directories that are ready to ingest (job.json present, no
    in-flight downloads)."""
    if not inbox.is_dir():
        return []
    ready: list[Path] = []
    for entry in sorted(inbox.iterdir()):
        if not entry.is_dir() or not (entry / JOB_FILENAME).is_file():
            continue
        # Quarantined drops keep their job.json; never re-select them, or one
        # poisoned drop re-fails on every scan and blocks the whole batch.
        if entry.name.endswith(FAILED_SUFFIX):
            continue
        in_flight = any(
            f.suffix.lower() in _IN_FLIGHT_SUFFIXES for f in entry.iterdir() if f.is_file()
        )
        if not in_flight:
            ready.append(entry)
    return ready


def ingest_folder(drop: Path, cfg: Config) -> IngestResult:
    """Move one drop's files into `<Evaluation>/<job.company>/`.

    Existing destination files are never overwritten — collisions get a
    ` (2)` style suffix. The consumed drop directory is removed."""
    job = JobRequest.model_validate_json((drop / JOB_FILENAME).read_text(encoding="utf-8"))
    dest = _safe_dest(cfg.evaluation_root, job.company)
    dest.mkdir(parents=True, exist_ok=True)

    moved: list[str] = []
    for src in sorted(drop.iterdir()):
        if not src.is_file() or src.name == JOB_FILENAME:
            continue
        # "Download all" from an AngelList dataroom arrives as a single zip;
        # unpack it so the individual deck/docs land in the company folder
        # where load_materials can classify them.
        if src.suffix.lower() == ".zip":
            moved.extend(_extract_zip(src, dest))
            src.unlink()
            continue
        target = _collision_free(dest / src.name)
        shutil.move(str(src), str(target))
        moved.append(target.name)

    (drop / JOB_FILENAME).unlink()
    _remove_if_empty(drop)

    missing_angellist = not any(_ANGELLIST_HINT in name.lower() for name in moved)
    missing_deck = not any(hint in name.lower() for name in moved for hint in _DECK_HINT)
    return IngestResult(
        job=job,
        folder=dest,
        moved=moved,
        missing_angellist=missing_angellist,
        missing_deck=missing_deck,
    )


def run_ingest(
    inbox: Path,
    cfg: Config,
    *,
    on_error: Callable[[Path, Exception], None] | None = None,
) -> list[IngestResult]:
    """Ingest every ready drop in the inbox, isolating failures.

    Each drop is processed independently: if one raises (malformed job.json,
    an invalid-char / traversal company name, a locked file mid-move), that
    drop is quarantined (renamed `<name>.failed` so it is never re-scanned)
    and the batch continues. Previously a single list comprehension aborted
    the whole batch on the first failure — dropping already-computed results
    and re-failing the poisoned drop on every 20s watch cycle forever.

    `on_error(drop, exc)` is invoked once per quarantined drop (the watch loop
    uses it to surface the failure); failures are always logged.
    """
    results: list[IngestResult] = []
    for drop in scan_inbox(inbox):
        try:
            results.append(ingest_folder(drop, cfg))
        except Exception as exc:
            logger.warning("quarantining unprocessable drop %s: %s", drop, exc)
            _quarantine(drop)
            if on_error is not None:
                on_error(drop, exc)
    return results


def _safe_dest(evaluation_root: Path, company: str) -> Path:
    """Resolve `<evaluation_root>/<sanitized company>` and prove it stays
    inside the Evaluation root. Raises ValueError on an empty/degenerate name
    or an attempted escape (`..`, absolute paths, separators)."""
    safe = _ILLEGAL_NAME_CHARS.sub(" ", company)
    safe = re.sub(r"\s+", " ", safe).strip().rstrip(".").strip()
    if not safe or safe in {".", ".."}:
        raise ValueError(f"company name sanitizes to an empty/invalid folder name: {company!r}")
    root = evaluation_root.resolve()
    dest = (root / safe).resolve()
    if dest != root and root not in dest.parents:
        raise ValueError(f"company name escapes the Evaluation root: {company!r}")
    return dest


def _quarantine(drop: Path) -> None:
    """Rename a failed drop to `<name>.failed` so scan_inbox skips it and the
    daemon stops re-failing on it. Best-effort — a locked dir just stays put
    (it will be retried next cycle; only a persistent failure keeps looping,
    and that is preferable to silently deleting a capture)."""
    target = drop.with_name(drop.name + FAILED_SUFFIX)
    counter = 2
    while target.exists():
        target = drop.with_name(f"{drop.name}{FAILED_SUFFIX}.{counter}")
        counter += 1
    with contextlib.suppress(OSError):
        drop.rename(target)


def _extract_zip(archive: Path, dest: Path) -> list[str]:
    """Flatten a zip's file members into `dest` (collision-free), skipping
    directories and archive cruft (`__MACOSX/`, dotfiles). Returns the names
    written. A corrupt zip is skipped with no members extracted."""
    written: list[str] = []
    try:
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = Path(info.filename).name
                if not name or name.startswith(".") or "__MACOSX" in info.filename:
                    continue
                target = _collision_free(dest / name)
                with zf.open(info) as member, target.open("wb") as out:
                    shutil.copyfileobj(member, out)
                written.append(target.name)
    except zipfile.BadZipFile:
        return written
    return written


def _collision_free(target: Path) -> Path:
    if not target.exists():
        return target
    counter = 2
    while True:
        candidate = target.with_name(f"{target.stem} ({counter}){target.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def _remove_if_empty(folder: Path) -> None:
    """Best-effort cleanup of the consumed drop dir.

    On Windows the now-empty directory can be transiently locked (Drive
    sync, the search indexer, or the browser's download manager still
    holding a handle), so a failed rmdir must be non-fatal — otherwise one
    locked dir aborts the whole ingest batch. An empty leftover is harmless:
    job.json is already gone, so scan_inbox won't re-ingest it."""
    if folder.is_dir() and not any(folder.iterdir()):
        with contextlib.suppress(OSError):
            folder.rmdir()
