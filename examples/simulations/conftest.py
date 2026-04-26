"""Shared fixtures + the ``live`` mark for scenario simulations."""

from __future__ import annotations

import os

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: requires a real Anthropic API key. Skipped unless "
        "PYHARNESS_LIVE_API=1 is set in the environment.",
    )


def pytest_collection_modifyitems(config, items):
    """Auto-skip ``live`` tests unless explicitly enabled."""

    if os.environ.get("PYHARNESS_LIVE_API") == "1":
        return
    skip_live = pytest.mark.skip(reason="set PYHARNESS_LIVE_API=1 to run live scenarios")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


@pytest.fixture
def isolated_session_dir(tmp_path, monkeypatch):
    """Per-test session log dir so scenarios don't pollute ~/.pyharness."""

    sessions = tmp_path / "sessions"
    sessions.mkdir()
    monkeypatch.setenv("PYHARNESS_SESSION_DIR", str(sessions))
    return sessions
