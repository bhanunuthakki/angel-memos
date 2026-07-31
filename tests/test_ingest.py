"""Inbox ingestion: job.json parsing, download-completeness guards, file
moves into the Evaluation folder, and collision handling. No Claude calls."""

import json
import stat
import zipfile
from pathlib import Path

import pytest

from angel_memos.config import Config
from angel_memos.ingest import (
    JOB_FILENAME,
    JobRequest,
    ingest_folder,
    prune_inbox,
    run_ingest,
    scan_inbox,
)


def _cfg(tmp_path: Path) -> Config:
    return Config(
        evaluation_root=tmp_path / "Evaluation",
        portfolio_root=tmp_path / "Portfolio",
    )


def _payload(filename: str) -> bytes:
    """Distinct bytes per document.

    Two genuinely different captured documents are never byte-identical, and
    ingest's dedupe pass treats identical bytes as the same document — so a
    fixture that gave the memo and the deck the same content would exercise a
    situation that cannot occur and mask the real behaviour."""
    return b"%PDF-1.4 fake " + filename.encode()


def _make_drop(
    inbox: Path,
    name: str = "Acme",
    files: list[str] | None = None,
    job: dict[str, object] | None = None,
) -> Path:
    drop = inbox / name
    drop.mkdir(parents=True)
    for filename in files if files is not None else ["angellist - Acme.pdf", "deck - Acme.pdf"]:
        (drop / filename).write_bytes(_payload(filename))
    payload: dict[str, object] = {"company": name, "tier": "quick"}
    if job is not None:
        payload = job
    (drop / JOB_FILENAME).write_text(json.dumps(payload), encoding="utf-8")
    return drop


# ---------------------------------------------------------------------------
# Job parsing.
# ---------------------------------------------------------------------------


def test_job_request_defaults() -> None:
    job = JobRequest.model_validate({"company": "Acme"})
    assert job.tier == "none"
    assert job.source_url == ""


def test_job_request_rejects_unknown_tier() -> None:
    import pytest

    with pytest.raises(Exception):
        JobRequest.model_validate({"company": "Acme", "tier": "exhaustive"})


# ---------------------------------------------------------------------------
# Inbox scanning.
# ---------------------------------------------------------------------------


def test_scan_inbox_finds_dirs_with_job_file(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    drop = _make_drop(inbox)
    assert scan_inbox(inbox) == [drop]


def test_scan_inbox_skips_dirs_without_job_file(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    (inbox / "NoJob").mkdir(parents=True)
    (inbox / "NoJob" / "deck.pdf").write_bytes(b"x")
    assert scan_inbox(inbox) == []


def test_scan_inbox_skips_in_flight_downloads(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    drop = _make_drop(inbox)
    (drop / "attachment.pdf.crdownload").write_bytes(b"partial")
    assert scan_inbox(inbox) == []


def test_scan_inbox_missing_inbox_is_empty(tmp_path: Path) -> None:
    assert scan_inbox(tmp_path / "nope") == []


# ---------------------------------------------------------------------------
# Folder ingestion.
# ---------------------------------------------------------------------------


def test_ingest_moves_files_into_evaluation(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    drop = _make_drop(tmp_path / "inbox")
    result = ingest_folder(drop, cfg)
    dest = cfg.evaluation_root / "Acme"
    assert result.folder == dest
    assert (dest / "angellist - Acme.pdf").is_file()
    assert (dest / "deck - Acme.pdf").is_file()
    assert result.job.tier == "quick"
    assert not drop.exists()  # consumed drop dir removed


def test_ingest_company_comes_from_job_not_dirname(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    drop = _make_drop(
        tmp_path / "inbox", name="acme-dl-1", job={"company": "Acme Robotics", "tier": "none"}
    )
    result = ingest_folder(drop, cfg)
    assert result.folder == cfg.evaluation_root / "Acme Robotics"


def test_ingest_flags_missing_angellist(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    drop = _make_drop(tmp_path / "inbox", files=["deck - Acme.pdf"])
    result = ingest_folder(drop, cfg)
    assert result.missing_angellist is True


def test_ingest_present_angellist_not_flagged(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    drop = _make_drop(tmp_path / "inbox")
    assert ingest_folder(drop, cfg).missing_angellist is False


def test_ingest_collision_appends_suffix_instead_of_overwriting(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    dest = cfg.evaluation_root / "Acme"
    dest.mkdir(parents=True)
    (dest / "deck - Acme.pdf").write_bytes(b"original")
    drop = _make_drop(tmp_path / "inbox", files=["deck - Acme.pdf"])
    ingest_folder(drop, cfg)
    assert (dest / "deck - Acme.pdf").read_bytes() == b"original"
    assert (dest / "deck - Acme (2).pdf").is_file()


def test_ingest_does_not_move_job_file(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    drop = _make_drop(tmp_path / "inbox")
    result = ingest_folder(drop, cfg)
    assert not (result.folder / JOB_FILENAME).exists()


def test_ingest_tolerates_unremovable_drop_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A locked (unremovable) drop dir must not abort the ingest — the files
    are already moved by then; only the empty-dir cleanup fails."""
    cfg = _cfg(tmp_path)
    drop = _make_drop(tmp_path / "inbox")

    def boom(self: Path) -> None:
        raise PermissionError("[WinError 5] Access is denied")

    monkeypatch.setattr(Path, "rmdir", boom)
    result = ingest_folder(drop, cfg)  # must NOT raise
    dest = cfg.evaluation_root / "Acme"
    assert (dest / "angellist - Acme.pdf").is_file()
    assert result.job.company == "Acme"


def _make_zip(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def test_ingest_unpacks_download_all_zip(tmp_path: Path) -> None:
    """A dataroom 'Download all' zip is unpacked so the deck + docs land loose
    in the company folder (and the zip itself is not kept)."""
    cfg = _cfg(tmp_path)
    drop = (tmp_path / "inbox" / "Acme").resolve()
    drop.mkdir(parents=True)
    (drop / "angellist - Acme.pdf").write_bytes(b"%PDF page")
    _make_zip(
        drop / "documents.zip",
        {"Acme Deck.pdf": b"%PDF deck", "Disclaimers.pdf": b"%PDF disc"},
    )
    (drop / JOB_FILENAME).write_text(json.dumps({"company": "Acme", "tier": "quick"}))

    result = ingest_folder(drop, cfg)
    dest = cfg.evaluation_root / "Acme"
    assert (dest / "Acme Deck.pdf").read_bytes() == b"%PDF deck"
    assert (dest / "Disclaimers.pdf").is_file()
    assert not (dest / "documents.zip").exists()  # zip consumed, not kept
    assert "Acme Deck.pdf" in result.moved


def test_ingest_zip_flattens_nested_dirs_and_skips_cruft(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    drop = (tmp_path / "inbox" / "Acme").resolve()
    drop.mkdir(parents=True)
    (drop / "angellist - Acme.pdf").write_bytes(b"%PDF")
    _make_zip(
        drop / "all.zip",
        {
            "sub/Deck.pdf": b"deck",
            "__MACOSX/._Deck.pdf": b"junk",
            ".DS_Store": b"junk",
        },
    )
    (drop / JOB_FILENAME).write_text(json.dumps({"company": "Acme", "tier": "none"}))

    result = ingest_folder(drop, cfg)
    dest = cfg.evaluation_root / "Acme"
    assert (dest / "Deck.pdf").read_bytes() == b"deck"
    assert not (dest / ".DS_Store").exists()
    assert result.moved.count("Deck.pdf") == 1


def test_ingest_corrupt_zip_is_skipped_not_fatal(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    drop = (tmp_path / "inbox" / "Acme").resolve()
    drop.mkdir(parents=True)
    (drop / "angellist - Acme.pdf").write_bytes(b"%PDF")
    (drop / "broken.zip").write_bytes(b"not really a zip")
    (drop / JOB_FILENAME).write_text(json.dumps({"company": "Acme", "tier": "none"}))

    result = ingest_folder(drop, cfg)  # must not raise
    dest = cfg.evaluation_root / "Acme"
    assert (dest / "angellist - Acme.pdf").is_file()
    assert result.missing_angellist is False


def test_run_ingest_processes_all_ready_drops(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    inbox = tmp_path / "inbox"
    _make_drop(inbox, name="Acme")
    _make_drop(inbox, name="Beta", job={"company": "Beta", "tier": "none"})
    results = run_ingest(inbox, cfg)
    assert sorted(r.job.company for r in results) == ["Acme", "Beta"]


# ---------------------------------------------------------------------------
# Fault isolation (#4): one bad drop must not abort the batch or wedge watch.
# ---------------------------------------------------------------------------


def test_run_ingest_isolates_and_quarantines_a_bad_drop(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    inbox = tmp_path / "inbox"
    # "AAA_bad" sorts BEFORE "Zeta" so, pre-fix, its failure aborted the batch
    # and Zeta was never reached.
    bad = _make_drop(inbox, name="AAA_bad", job={"company": ""})  # empty -> invalid
    _make_drop(inbox, name="Zeta", job={"company": "Zeta", "tier": "none"})

    errors: list[tuple[Path, Exception]] = []
    results = run_ingest(inbox, cfg, on_error=lambda d, e: errors.append((d, e)))

    # The good drop still processed despite the bad one sorting first.
    assert [r.job.company for r in results] == ["Zeta"]
    assert len(errors) == 1
    # The bad drop was quarantined so it will not be re-scanned.
    assert not bad.exists()
    assert (inbox / "AAA_bad.failed").is_dir()
    assert scan_inbox(inbox) == []  # quarantined dir is skipped


def test_scan_inbox_skips_quarantined_drops(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    drop = _make_drop(inbox, name="Acme")
    drop.rename(inbox / "Acme.failed")
    assert scan_inbox(inbox) == []


# ---------------------------------------------------------------------------
# Company-name sanitization + traversal guard (#6).
# ---------------------------------------------------------------------------


def test_ingest_sanitizes_illegal_chars_in_company_name(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    drop = _make_drop(tmp_path / "inbox", name="drop", job={"company": "Re:Build", "tier": "none"})
    result = ingest_folder(drop, cfg)
    # Colon replaced; folder is a single component directly under Evaluation.
    assert result.folder.parent == cfg.evaluation_root.resolve()
    assert ":" not in result.folder.name


def test_ingest_neutralizes_traversal_separators_and_stays_in_root(tmp_path: Path) -> None:
    """A name like '../../evil' has its separators replaced, so it can't
    escape — it lands as a single harmless folder directly under Evaluation."""
    cfg = _cfg(tmp_path)
    drop = _make_drop(
        tmp_path / "inbox", name="drop", job={"company": "../../evil", "tier": "none"}
    )
    result = ingest_folder(drop, cfg)
    root = cfg.evaluation_root.resolve()
    assert result.folder.parent == root  # single component, inside the root
    assert root in result.folder.parents or result.folder.parent == root
    # No directory was created outside the Evaluation root.
    assert not (root.parent / "evil").exists()


def test_ingest_rejects_degenerate_dot_name(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    drop = _make_drop(tmp_path / "inbox", name="drop", job={"company": "..", "tier": "none"})
    with pytest.raises(ValueError):
        ingest_folder(drop, cfg)


def test_ingest_degenerate_drop_is_quarantined_by_run_ingest(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    inbox = tmp_path / "inbox"
    _make_drop(inbox, name="evil", job={"company": "..", "tier": "none"})
    results = run_ingest(inbox, cfg)
    assert results == []
    assert (inbox / "evil.failed").is_dir()


# ---------------------------------------------------------------------------
# Deck-presence flag (#5).
# ---------------------------------------------------------------------------


def test_ingest_flags_missing_deck(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    drop = _make_drop(tmp_path / "inbox", files=["angellist - Acme.pdf"])
    assert ingest_folder(drop, cfg).missing_deck is True


def test_ingest_present_deck_not_flagged(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    drop = _make_drop(tmp_path / "inbox")  # default files include a deck
    assert ingest_folder(drop, cfg).missing_deck is False


# ---------------------------------------------------------------------------
# Byte-identity dedupe: a re-capture of an already-ingested deal must not
# accumulate " (2)" copies. Identity is the file's bytes, never its name.
# ---------------------------------------------------------------------------


def _prepopulated_acme(cfg: Config) -> Path:
    """An Acme folder already holding the default drop's two documents."""
    dest = cfg.evaluation_root / "Acme"
    dest.mkdir(parents=True)
    for filename in ("angellist - Acme.pdf", "deck - Acme.pdf"):
        (dest / filename).write_bytes(_payload(filename))
    return dest


def test_ingest_drops_byte_identical_file_instead_of_suffixing(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    dest = cfg.evaluation_root / "Acme"
    dest.mkdir(parents=True)
    (dest / "deck - Acme.pdf").write_bytes(_payload("deck - Acme.pdf"))
    drop = _make_drop(tmp_path / "inbox", files=["deck - Acme.pdf"])

    result = ingest_folder(drop, cfg)

    assert not (dest / "deck - Acme (2).pdf").exists()
    assert (dest / "deck - Acme.pdf").read_bytes() == _payload("deck - Acme.pdf")
    assert result.moved == []
    assert "deck - Acme.pdf" in result.deduped


def test_ingest_dedupes_identical_bytes_under_a_different_name(tmp_path: Path) -> None:
    """The OneNav case: the same PDF re-captured under the extension's naming
    must be recognised by content, not filename."""
    cfg = _cfg(tmp_path)
    dest = cfg.evaluation_root / "Acme"
    dest.mkdir(parents=True)
    (dest / "Acme Series C Deck.pdf").write_bytes(_payload("Acme deck.pdf"))
    drop = _make_drop(tmp_path / "inbox", files=["Acme deck.pdf"])

    result = ingest_folder(drop, cfg)

    assert not (dest / "Acme deck.pdf").exists()
    assert result.deduped == ["Acme deck.pdf"]


def test_ingest_keeps_same_named_file_with_different_bytes(tmp_path: Path) -> None:
    """Guard against over-deletion: a re-capture whose bytes differ is a
    DIFFERENT document and must be kept, suffixed."""
    cfg = _cfg(tmp_path)
    dest = cfg.evaluation_root / "Acme"
    dest.mkdir(parents=True)
    (dest / "deck - Acme.pdf").write_bytes(b"original bytes")
    drop = _make_drop(tmp_path / "inbox", files=["deck - Acme.pdf"])

    result = ingest_folder(drop, cfg)

    assert (dest / "deck - Acme.pdf").read_bytes() == b"original bytes"
    assert (dest / "deck - Acme (2).pdf").is_file()
    assert result.deduped == []


def test_ingest_dedupes_identical_members_within_one_drop(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    drop = tmp_path / "inbox" / "Acme"
    drop.mkdir(parents=True)
    (drop / "angellist - Acme.pdf").write_bytes(b"%PDF memo")
    (drop / "deck.pdf").write_bytes(b"%PDF deck")
    (drop / "deck-copy.pdf").write_bytes(b"%PDF deck")
    (drop / JOB_FILENAME).write_text(json.dumps({"company": "Acme", "tier": "none"}))

    result = ingest_folder(drop, cfg)

    dest = cfg.evaluation_root / "Acme"
    # Exactly one copy of the deck bytes survives; which name wins is the
    # traversal order, not a guarantee worth pinning.
    survivors = sorted(p.name for p in dest.iterdir())
    assert len(survivors) == 2
    assert "angellist - Acme.pdf" in survivors
    assert len(result.deduped) == 1
    assert result.deduped[0] in {"deck.pdf", "deck-copy.pdf"}


def test_ingest_dedupes_zip_members_against_existing_files(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    dest = cfg.evaluation_root / "Acme"
    dest.mkdir(parents=True)
    (dest / "Deck.pdf").write_bytes(b"%PDF deck")
    drop = tmp_path / "inbox" / "Acme"
    drop.mkdir(parents=True)
    _make_zip(drop / "all.zip", {"Deck.pdf": b"%PDF deck"})
    (drop / JOB_FILENAME).write_text(json.dumps({"company": "Acme", "tier": "none"}))

    ingest_folder(drop, cfg)

    assert not (dest / "Deck (2).pdf").exists()


def test_fully_deduped_drop_is_still_consumed(tmp_path: Path) -> None:
    """A wholly redundant re-capture leaves no drop behind to re-scan."""
    cfg = _cfg(tmp_path)
    dest = _prepopulated_acme(cfg)
    drop = _make_drop(tmp_path / "inbox")

    result = ingest_folder(drop, cfg)

    assert not drop.exists()
    assert sorted(result.deduped) == ["angellist - Acme.pdf", "deck - Acme.pdf"]
    assert sorted(p.name for p in dest.iterdir()) == [
        "angellist - Acme.pdf",
        "deck - Acme.pdf",
    ]


def test_deduped_files_still_count_toward_material_presence(tmp_path: Path) -> None:
    """A re-capture of an already-complete folder is not 'missing' its deck."""
    cfg = _cfg(tmp_path)
    _prepopulated_acme(cfg)
    drop = _make_drop(tmp_path / "inbox")

    result = ingest_folder(drop, cfg)

    assert result.missing_angellist is False
    assert result.missing_deck is False


# ---------------------------------------------------------------------------
# Inbox cleanup: consumed/empty drop dirs must not accumulate in Downloads.
# ---------------------------------------------------------------------------


def test_prune_inbox_removes_empty_leftover_dirs(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    stale = inbox / "Lazarus Energy"
    stale.mkdir(parents=True)
    (inbox / "Keep").mkdir()
    (inbox / "Keep" / "angellist - Keep.pdf").write_bytes(b"%PDF")

    pruned = prune_inbox(inbox)

    assert pruned == ["Lazarus Energy"]
    assert not stale.exists()
    assert (inbox / "Keep").is_dir()


def test_prune_inbox_keeps_dirs_holding_uningested_files(tmp_path: Path) -> None:
    """A drop with files but no job.json is an incomplete capture, not litter —
    pruning it would silently discard a real capture."""
    inbox = tmp_path / "inbox"
    partial = inbox / "Dexterity"
    partial.mkdir(parents=True)
    (partial / "angellist - Dexterity.pdf").write_bytes(b"%PDF")

    assert prune_inbox(inbox) == []
    assert partial.is_file() is False and partial.is_dir()


def test_run_ingest_prunes_consumed_dirs(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    inbox = tmp_path / "inbox"
    (inbox / "Stale").mkdir(parents=True)
    _make_drop(inbox, name="Acme")

    run_ingest(inbox, cfg)

    assert not (inbox / "Stale").exists()


def test_prune_inbox_removes_readonly_drop_dir(tmp_path: Path) -> None:
    """Chrome marks every drop dir ReadOnly, and Windows RemoveDirectory then
    fails with WinError 5 even on an empty dir — which silently stranded every
    consumed drop in Downloads."""
    inbox = tmp_path / "inbox"
    stale = inbox / "Lazarus Energy"
    stale.mkdir(parents=True)
    stale.chmod(stat.S_IREAD)
    try:
        assert prune_inbox(inbox) == ["Lazarus Energy"]
        assert not stale.exists()
    finally:
        if stale.exists():
            stale.chmod(stat.S_IWRITE)


def test_consumed_readonly_drop_is_removed_by_ingest(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    drop = _make_drop(tmp_path / "inbox")
    drop.chmod(stat.S_IREAD)
    try:
        ingest_folder(drop, cfg)
        assert not drop.exists()
    finally:
        if drop.exists():
            drop.chmod(stat.S_IWRITE)


def test_prune_inbox_tolerates_locked_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inbox = tmp_path / "inbox"
    (inbox / "Stale").mkdir(parents=True)

    def boom(self: Path) -> None:
        raise PermissionError("[WinError 5] Access is denied")

    monkeypatch.setattr(Path, "rmdir", boom)
    assert prune_inbox(inbox) == []  # must not raise
