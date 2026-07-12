"""Config loader: defaults are baked in; TOML overrides selectively."""

from pathlib import Path

import pytest

from angel_memos.config import Config, load_config


def test_config_has_defaults() -> None:
    cfg = Config()
    assert cfg.evaluation_root.name == "Evaluation"
    assert cfg.portfolio_root.name == "Portfolio"
    assert cfg.public_doc_id
    assert cfg.private_doc_id


def test_load_config_returns_defaults_when_no_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANGEL_MEMOS_CONFIG_DIR", str(tmp_path))
    cfg = load_config()
    assert cfg == Config()


def test_load_config_overrides_from_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANGEL_MEMOS_CONFIG_DIR", str(tmp_path))
    eval_root = tmp_path / "Evaluation"
    eval_root.mkdir()
    (tmp_path / "config.toml").write_text(
        f'evaluation_root = "{eval_root.as_posix()}"\npublic_doc_id = "custom_public_id"\n'
    )
    cfg = load_config()
    assert cfg.evaluation_root == eval_root
    assert cfg.public_doc_id == "custom_public_id"
    # Untouched fields keep defaults.
    assert cfg.private_doc_id == Config().private_doc_id
