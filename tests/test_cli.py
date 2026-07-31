"""CLI helpers that don't shell out — folder resolution and flat-file
auto-migration matching."""

from pathlib import Path

from angel_memos.cli import _flat_matches, _resolve_company_folder
from angel_memos.config import Config


def _touch(path: Path) -> None:
    path.write_bytes(b"%PDF-1.4 fake")


def test_flat_matches_finds_artifact_files_for_company(tmp_path: Path) -> None:
    _touch(tmp_path / "Acme AL Details.pdf")
    _touch(tmp_path / "Acme Deck.pdf")
    _touch(tmp_path / "Acme call notes.txt")
    matches = {p.name for p in _flat_matches(tmp_path, "Acme")}
    assert matches == {"Acme AL Details.pdf", "Acme Deck.pdf", "Acme call notes.txt"}


def test_flat_matches_does_not_sweep_longer_company_prefix(tmp_path: Path) -> None:
    """Resolving 'Acme' must NOT grab 'Acme Robotics' files — the token after
    'Acme ' is 'Robotics', not a materials-artifact word."""
    _touch(tmp_path / "Acme AL.pdf")
    _touch(tmp_path / "Acme Robotics AL.pdf")
    _touch(tmp_path / "Acme Robotics Deck.pdf")
    matches = {p.name for p in _flat_matches(tmp_path, "Acme")}
    assert matches == {"Acme AL.pdf"}


def test_flat_matches_resolves_the_longer_company_correctly(tmp_path: Path) -> None:
    _touch(tmp_path / "Acme AL.pdf")
    _touch(tmp_path / "Acme Robotics AL.pdf")
    matches = {p.name for p in _flat_matches(tmp_path, "Acme Robotics")}
    assert matches == {"Acme Robotics AL.pdf"}


def test_flat_matches_ignores_non_material_suffixes(tmp_path: Path) -> None:
    _touch(tmp_path / "Acme AL.pdf")
    (tmp_path / "Acme spreadsheet.xlsx").write_bytes(b"x")
    matches = {p.name for p in _flat_matches(tmp_path, "Acme")}
    assert matches == {"Acme AL.pdf"}


# ---------------------------------------------------------------------------
# Name resolution across the three roots: active deals win over the archive,
# but an archived pass must still resolve by name.
# ---------------------------------------------------------------------------


def _cfg(tmp_path: Path) -> Config:
    return Config(
        evaluation_root=tmp_path / "Evaluation",
        portfolio_root=tmp_path / "Portfolio",
        passed_root=tmp_path / "Passed",
    )


def test_passed_folder_resolves_when_no_active_deal(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    archived = cfg.passed_root / "OneNav"
    archived.mkdir(parents=True)
    assert _resolve_company_folder("OneNav", None, cfg) == archived


def test_evaluation_wins_over_passed(tmp_path: Path) -> None:
    """A re-opened deal (fresh Evaluation capture) must not resolve to the
    stale archive."""
    cfg = _cfg(tmp_path)
    (cfg.passed_root / "Acme").mkdir(parents=True)
    active = cfg.evaluation_root / "Acme"
    active.mkdir(parents=True)
    assert _resolve_company_folder("Acme", None, cfg) == active
