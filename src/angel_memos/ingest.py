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
import hashlib
import json
import logging
import re
import shutil
import stat
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from angel_memos.angellist import RoundId, read_round
from angel_memos.config import Config
from angel_memos.materials import ANGELLIST_CACHE_FILENAME
from angel_memos.models import Stage

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

# Streaming hash block. Decks run to several MB; never slurp a PDF whole.
_HASH_CHUNK_BYTES = 1 << 20


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
    # Source names discarded as byte-identical to a file already in the company
    # folder. These still evidence the material's presence, so the missing_*
    # flags are computed over moved + deduped.
    deduped: list[str] = Field(default_factory=list)


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

    Existing destination files are never overwritten — a same-named file with
    DIFFERENT bytes gets a ` (2)` style suffix, while one whose bytes already
    exist in the folder is discarded rather than copied under a second name.
    The consumed drop directory is removed."""
    # Chrome's Windows download flow can mark the directory read-only. On POSIX,
    # the equivalent mode also removes directory traversal, so restore the
    # owner's minimum access before reading and consuming the completed drop.
    drop.chmod(drop.stat().st_mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    job = JobRequest.model_validate_json((drop / JOB_FILENAME).read_text(encoding="utf-8"))
    dest = _resolve_dest(cfg.evaluation_root, job.company, drop)
    dest.mkdir(parents=True, exist_ok=True)

    index = _ContentIndex(dest)
    moved: list[str] = []
    deduped: list[str] = []
    for src in sorted(drop.iterdir()):
        if not src.is_file() or src.name == JOB_FILENAME:
            continue
        # "Download all" from an AngelList dataroom arrives as a single zip;
        # unpack it so the individual deck/docs land in the company folder
        # where load_materials can classify them.
        if src.suffix.lower() == ".zip":
            extracted, redundant = _extract_zip(src, dest, index)
            moved.extend(extracted)
            deduped.extend(redundant)
            src.unlink()
            continue
        if index.match(src) is not None:
            # Re-capture of a document already held. Identity is the bytes, so
            # this cannot discard a revised deck that merely kept its filename.
            src.unlink()
            deduped.append(src.name)
            continue
        target = _collision_free(dest / src.name)
        shutil.move(str(src), str(target))
        index.add(target)
        moved.append(target.name)

    (drop / JOB_FILENAME).unlink()
    _remove_if_empty(drop)

    seen = moved + deduped
    missing_angellist = not any(_ANGELLIST_HINT in name.lower() for name in seen)
    missing_deck = not any(hint in name.lower() for name in seen for hint in _DECK_HINT)
    if deduped:
        logger.info("dropped %d byte-identical re-capture(s) into %s", len(deduped), dest)
    return IngestResult(
        job=job,
        folder=dest,
        moved=moved,
        missing_angellist=missing_angellist,
        missing_deck=missing_deck,
        deduped=deduped,
    )


class _ContentIndex:
    """Byte-identity index over a company folder.

    Content, not filename, decides identity: the extension names a re-capture
    `<Company> deck.pdf` while an earlier manual save may be
    `<Company> Series C Deck.pdf`. Hashing is lazy and bucketed by size, so the
    common case (no size peer) costs one `stat` and reads nothing.
    """

    def __init__(self, folder: Path) -> None:
        self._by_size: dict[int, list[Path]] = {}
        self._digests: dict[Path, str] = {}
        if folder.is_dir():
            for entry in folder.iterdir():
                if entry.is_file():
                    self.add(entry)

    def add(self, path: Path) -> None:
        with contextlib.suppress(OSError):
            self._by_size.setdefault(path.stat().st_size, []).append(path)

    def match(self, candidate: Path) -> Path | None:
        """The indexed file with identical bytes, or None."""
        try:
            peers = self._by_size.get(candidate.stat().st_size)
        except OSError:
            return None
        if not peers:
            return None
        wanted = _sha256(candidate)
        if wanted is None:
            return None
        for peer in peers:
            if self._digest(peer) == wanted:
                return peer
        return None

    def _digest(self, path: Path) -> str | None:
        if path not in self._digests:
            digest = _sha256(path)
            if digest is None:
                return None
            self._digests[path] = digest
        return self._digests[path]


def _sha256(path: Path) -> str | None:
    """Streaming digest, or None if the file can't be read (a Drive-sync lock
    must degrade to 'not a known duplicate', never to a wrong match)."""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(_HASH_CHUNK_BYTES):
                digest.update(chunk)
    except OSError as exc:
        logger.warning("cannot hash %s for dedupe: %s", path, exc)
        return None
    return digest.hexdigest()


def prune_inbox(inbox: Path) -> list[str]:
    """Delete empty drop directories left behind in the Downloads inbox.

    Only *empty* directories go: a drop still holding files is either mid-
    capture or an extension run that never wrote job.json, and discarding it
    would destroy a real capture. Returns the removed directory names."""
    if not inbox.is_dir():
        return []
    removed: list[str] = []
    for entry in sorted(inbox.iterdir()):
        if not entry.is_dir():
            continue
        try:
            if any(entry.iterdir()):
                continue
            _rmdir(entry)
        except OSError as exc:
            # Drive sync / the indexer can hold a transient handle; a leftover
            # empty dir is inert, so never let cleanup fail an ingest batch.
            logger.debug("could not prune %s: %s", entry, exc)
            continue
        removed.append(entry.name)
    return removed


def _rmdir(folder: Path) -> None:
    """Remove an empty directory, defeating the Windows ReadOnly attribute.

    Chrome creates each `Downloads/angel-memos/<Company>/` drop directory with
    the ReadOnly attribute set, and Windows' RemoveDirectory refuses a
    read-only directory with WinError 5 even when it is empty — so every
    consumed drop used to be left behind forever. Clearing the attribute and
    retrying is the documented fix; a still-locked directory raises and is
    handled by the caller."""
    try:
        folder.rmdir()
    except PermissionError:
        folder.chmod(stat.S_IWRITE)
        folder.rmdir()


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
    # Sweep consumed/empty drop dirs every pass so the Downloads inbox stays
    # clean on an ongoing basis, not just when a Windows rmdir happens to win.
    for name in prune_inbox(inbox):
        logger.info("pruned empty drop dir %s", name)
    return results


def _resolve_dest(evaluation_root: Path, company: str, drop: Path) -> Path:
    """The company folder this drop belongs in, splitting rounds apart.

    The same company comes back round after round, and those are separate
    deals: merging a Series B capture into the Series A folder mixes two sets
    of terms under one `decision.md`. So when the incoming AL memo names a
    round that no existing folder holds, this returns a round-suffixed sibling
    — `Acme (Series B)` — rather than the bare name.

    It forks only on positive evidence of a DIFFERENT round. An unreadable
    round on either side merges as before, because a wrong split scatters one
    deal across two folders, which is worse than a merge the byte-dedupe pass
    already de-duplicates."""
    base = _safe_dest(evaluation_root, company)
    if not _holds_files(base):
        return base
    incoming = _drop_round(drop)
    if incoming is None:
        return base

    unknown: Path | None = None
    for candidate in [base, *sorted(base.parent.glob(f"{base.name} (*)"))]:
        if not candidate.is_dir():
            continue
        stage = _folder_stage(candidate)
        if stage == incoming.stage:
            return candidate
        if stage is None and unknown is None:
            unknown = candidate
    if unknown is not None:
        return unknown
    logger.info("%s is a new round (%s); ingesting alongside", company, incoming.label)
    return _safe_dest(evaluation_root, f"{company} ({incoming.label})")


def _holds_files(folder: Path) -> bool:
    return folder.is_dir() and any(f.is_file() for f in folder.iterdir())


def _drop_round(drop: Path) -> RoundId | None:
    """The round named by the drop's AngelList memo, if it has a text layer."""
    for entry in sorted(drop.iterdir()):
        if entry.is_file() and _ANGELLIST_HINT in entry.name.lower():
            return read_round(entry)
    return None


def _folder_stage(folder: Path) -> Stage | None:
    """The stage a company folder already holds.

    Prefers `.angellist_cache.json`, whose `stage` came from the full
    (vision-capable) extraction, over re-reading the PDF text layer — the
    cache is populated even for image-only memos that have no text to parse."""
    cached = folder / ANGELLIST_CACHE_FILENAME
    if cached.is_file():
        try:
            raw = json.loads(cached.read_text(encoding="utf-8"))
            return Stage(raw["stage"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.debug("unusable stage in %s: %s", cached, exc)
    for entry in sorted(folder.iterdir()):
        if entry.is_file() and _ANGELLIST_HINT in entry.name.lower():
            found = read_round(entry)
            if found is not None:
                return found.stage
    return None


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


def _extract_zip(archive: Path, dest: Path, index: _ContentIndex) -> tuple[list[str], list[str]]:
    """Flatten a zip's file members into `dest` (collision-free), skipping
    directories and archive cruft (`__MACOSX/`, dotfiles). Returns
    `(written, deduped)` — a member whose bytes already exist in `dest` is
    extracted, recognised, and removed rather than kept as a second copy. A
    corrupt zip is skipped with no members extracted."""
    written: list[str] = []
    deduped: list[str] = []
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
                # Hash after landing: a zip member has no stable on-disk
                # identity until written, and streaming it twice would cost
                # a second decompression pass.
                if index.match(target) is not None:
                    target.unlink()
                    deduped.append(name)
                    continue
                index.add(target)
                written.append(target.name)
    except zipfile.BadZipFile:
        return written, deduped
    return written, deduped


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
            _rmdir(folder)
