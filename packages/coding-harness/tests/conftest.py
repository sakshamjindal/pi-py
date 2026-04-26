"""Coding-harness pytest fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure both packages' src/ directories are importable when tests run
# without an editable install.
ROOT = Path(__file__).resolve().parent.parent.parent.parent
CODING_HARNESS_SRC = ROOT / "packages" / "coding-harness" / "src"
SDK_SRC = ROOT / "packages" / "pyharness-sdk" / "src"
for path in (SDK_SRC, CODING_HARNESS_SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


@pytest.fixture
def isolated_session_dir(tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    monkeypatch.setenv("PYHARNESS_SESSION_DIR", str(sessions))
    return sessions
