"""TUI-package pytest fixtures.

Adds all three package src/ directories to sys.path so tests can
import `pyharness`, `coding_harness`, and `pyharness_tui` even
without an editable install.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent.parent
for path in (
    ROOT / "packages" / "pyharness-sdk" / "src",
    ROOT / "packages" / "coding-harness" / "src",
    ROOT / "packages" / "tui" / "src",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


@pytest.fixture
def isolated_session_dir(tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    monkeypatch.setenv("PYHARNESS_SESSION_DIR", str(sessions))
    return sessions
