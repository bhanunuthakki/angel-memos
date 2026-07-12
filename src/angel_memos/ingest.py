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

import shutil
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from angel_memos.config import Config

JOB_FILENAME = "job.json"

_IN_FLIGHT_SUFFIXES: frozenset[str] = frozenset({".crdownload", ".tmp", ".partial"})

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


def scan_inbox(inbox: Path) -> list[Path]:
    """Drop directories that are ready to ingest (job.json present, no
    in-flight downloads)."""
    if not inbox.is_dir():
        return []
    ready: list[Path] = []
    for entry in sorted(inbox.iterdir()):
        if not entry.is_dir() or not (entry / JOB_FILENAME).is_file():
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
    dest = cfg.evaluation_root / job.company
    dest.mkdir(parents=True, exist_ok=True)

    moved: list[str] = []
    for src in sorted(drop.iterdir()):
        if not src.is_file() or src.name == JOB_FILENAME:
            continue
        target = _collision_free(dest / src.name)
        shutil.move(str(src), str(target))
        moved.append(target.name)

    (drop / JOB_FILENAME).unlink()
    _remove_if_empty(drop)

    missing_angellist = not any(_ANGELLIST_HINT in name.lower() for name in moved)
    return IngestResult(job=job, folder=dest, moved=moved, missing_angellist=missing_angellist)


def run_ingest(inbox: Path, cfg: Config) -> list[IngestResult]:
    """Ingest every ready drop in the inbox."""
    return [ingest_folder(drop, cfg) for drop in scan_inbox(inbox)]


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
    if folder.is_dir() and not any(folder.iterdir()):
        folder.rmdir()
