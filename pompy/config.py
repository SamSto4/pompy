"""Where Pompy reads from and writes to. Both can be overridden with env vars (tests do this)."""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def source_dir() -> Path:
    """Claude Code's transcript folder."""
    return Path(os.environ.get("POMPY_SOURCE", Path.home() / ".claude" / "projects"))


def data_dir() -> Path:
    """Pompy's private, git-ignored store: raw transcript copies + the SQLite memory database."""
    return Path(os.environ.get("POMPY_DATA", REPO_ROOT / "data"))


def out_dir() -> Path:
    """Generated insights (git-ignored). The Dashboard reads from here."""
    return Path(os.environ.get("POMPY_OUT", REPO_ROOT / "out"))
