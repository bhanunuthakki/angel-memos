"""Runtime configuration: paths to the Drive folders and Google Doc IDs.

Read from `%USERPROFILE%\\.config\\angel-memos\\config.toml` if present; falls
back to baked-in defaults that match the user's current Drive layout."""

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


# Defaults derived from the user's current Drive setup. The Google Doc IDs
# come from the URLs shared at design time (Public/Private memo docs).
def _default_investing_root() -> Path:
    if os.name == "nt":
        return Path(r"G:\My Drive\Personal Finances\Angel Investing")
    cloud_storage = Path.home() / "Library" / "CloudStorage"
    drive_roots = sorted(cloud_storage.glob("GoogleDrive-*/My Drive"))
    drive_root = drive_roots[0] if len(drive_roots) == 1 else Path.home() / "Documents"
    return drive_root / "Personal Finances" / "Angel Investing"


_DEFAULT_INVESTING_ROOT = _default_investing_root()
_DEFAULT_EVALUATION_ROOT = _DEFAULT_INVESTING_ROOT / "Evaluation"
_DEFAULT_PORTFOLIO_ROOT = _DEFAULT_INVESTING_ROOT / "Portfolio"
_DEFAULT_PASSED_ROOT = _DEFAULT_INVESTING_ROOT / "Passed"
_DEFAULT_PUBLIC_DOC_ID = "1nyFj17M4kktlHD028AVF9D-8zeyGRGKC-MXzHNfXA80"
_DEFAULT_PRIVATE_DOC_ID = "1Ntm55VRReWGTRz4nxm5jK35T_YOlam0Y_0rIxOmYydw"

_CONFIG_DIR_ENV_VAR = "ANGEL_MEMOS_CONFIG_DIR"
_DEFAULT_CONFIG_FILENAME = "config.toml"


class Config(BaseModel):
    """Static paths and IDs that don't change between deals."""

    model_config = ConfigDict(frozen=True)

    evaluation_root: Path = Field(default=_DEFAULT_EVALUATION_ROOT)
    portfolio_root: Path = Field(default=_DEFAULT_PORTFOLIO_ROOT)
    # Archive for deals decided `pass`: `mv Evaluation/<C> Passed/<C>`. Read
    # for name resolution and calibration scans; never a write destination
    # for pipeline phases.
    passed_root: Path = Field(default=_DEFAULT_PASSED_ROOT)
    public_doc_id: str = Field(default=_DEFAULT_PUBLIC_DOC_ID, min_length=1)
    private_doc_id: str = Field(default=_DEFAULT_PRIVATE_DOC_ID, min_length=1)


def config_dir() -> Path:
    """Where to look for `config.toml` and Google OAuth tokens."""
    override = os.environ.get(_CONFIG_DIR_ENV_VAR)
    if override:
        return Path(override)
    return Path.home() / ".config" / "angel-memos"


def load_config() -> Config:
    """Read config.toml if present; otherwise return defaults."""
    path = config_dir() / _DEFAULT_CONFIG_FILENAME
    if not path.is_file():
        return Config()
    with path.open("rb") as f:
        raw = tomllib.load(f)
    return Config.model_validate(raw)
