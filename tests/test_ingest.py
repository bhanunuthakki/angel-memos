"""Inbox ingestion: job.json parsing, download-completeness guards, file
moves into the Evaluation folder, and collision handling. No Claude calls."""

import json
from pathlib import Path

from angel_memos.config import Config
from angel_memos.ingest import (
    JOB_FILENAME,
    JobRequest,
    ingest_folder,
    run_ingest,
    scan_inbox,
)


def _cfg(tmp_path: Path) -> Config:
    return Config(
        evaluation_root=tmp_path / "Evaluation",
        portfolio_root=tmp_path / "Portfolio",
    )


def _make_drop(
    inbox: Path,
    name: str = "Acme",
    files: list[str] | None = None,
    job: dict[str, object] | None = None,
) -> Path:
    drop = inbox / name
    drop.mkdir(parents=True)
    for filename in files if files is not None else ["angellist - Acme.pdf", "deck - Acme.pdf"]:
        (drop / filename).write_bytes(b"%PDF-1.4 fake")
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


def test_run_ingest_processes_all_ready_drops(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    inbox = tmp_path / "inbox"
    _make_drop(inbox, name="Acme")
    _make_drop(inbox, name="Beta", job={"company": "Beta", "tier": "none"})
    results = run_ingest(inbox, cfg)
    assert sorted(r.job.company for r in results) == ["Acme", "Beta"]
