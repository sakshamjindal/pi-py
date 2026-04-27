"""Shared pytest fixtures + the ``live`` mark for scenario simulations.

Live scenarios hit a real LLM and are skipped by default. Enable with
``PYHARNESS_LIVE_API=1`` (typically alongside ``OPENROUTER_API_KEY`` or
``ANTHROPIC_API_KEY``).
"""

from __future__ import annotations

import os

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: requires a real LLM. Skipped unless PYHARNESS_LIVE_API=1.",
    )


def pytest_collection_modifyitems(config, items):
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


@pytest.fixture
def live_model() -> str:
    """The model used for live scenarios. Override with PYHARNESS_LIVE_MODEL."""

    return os.environ.get(
        "PYHARNESS_LIVE_MODEL",
        "openrouter/anthropic/claude-haiku-4-5",
    )
