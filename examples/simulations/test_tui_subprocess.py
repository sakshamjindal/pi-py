"""Subprocess-level TUI tests.

Exercises the `pyharness-tui` console script as users actually run it.
The mock-mode tests in packages/tui/tests cover the in-process happy
paths; this file proves the actual entry point is wired and that
fresh-dir UX is friendly.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def _tui_cmd() -> list[str]:
    """Use the in-process console script if available, else `python -m
    pyharness_tui.cli`."""

    return [sys.executable, "-m", "pyharness_tui.cli"]


def test_tui_subprocess_no_project_friendly_error(tmp_path):
    """Running pyharness-tui as a subprocess in a fresh dir prints a
    friendly error to stderr and exits with rc=2 (not a Python
    traceback)."""

    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "fakehome")
    (tmp_path / "fakehome").mkdir()

    result = subprocess.run(
        [*_tui_cmd(), "any prompt here"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 2, f"expected rc=2, got {result.returncode}"
    # Friendly: NoProjectError message went to stderr without traceback.
    assert "Traceback" not in result.stderr, "should not show a Python traceback"
    assert "No project found" in result.stderr
    assert "pyharness init" in result.stderr


def test_tui_subprocess_help_works(tmp_path):
    """`pyharness-tui --help` works without env / project setup."""

    result = subprocess.run(
        [*_tui_cmd(), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert "pyharness-tui" in result.stdout
    assert "--bare" in result.stdout
    assert "--workspace" in result.stdout
    assert "--model" in result.stdout


def test_tui_subprocess_repl_eof_exits_clean(tmp_path):
    """REPL with closed stdin (EOF immediately) exits cleanly."""

    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "fakehome")
    (tmp_path / "fakehome").mkdir()
    (tmp_path / ".pyharness").mkdir()

    result = subprocess.run(
        _tui_cmd(),
        cwd=tmp_path,
        env=env,
        input="",  # immediate EOF
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert "Traceback" not in result.stderr
    # Banner is printed even with EOF.
    assert "workspace=" in result.stderr
