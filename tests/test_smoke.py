"""Smoke test: the package imports and the CLI is registered.

This guards Stage 1 — the structure is in place and the CLI entry point is
wired up. Real subsystem tests land alongside their stages.
"""

from __future__ import annotations

import importlib


def test_package_imports():
    pkg = importlib.import_module("pyharness")
    assert pkg.__version__


def test_cli_main_requires_prompt(capsys):
    from pyharness.cli import main

    rc = main([])
    captured = capsys.readouterr()
    assert rc == 2
    assert "no prompt" in captured.err.lower()


def test_subsystem_modules_import():
    # All planned subsystem modules should at least be importable so that
    # later stages can fill them in without circular-import surprises.
    for name in (
        "pyharness.cli",
        "pyharness.harness",
        "pyharness.llm",
        "pyharness.session",
        "pyharness.workspace",
        "pyharness.config",
        "pyharness.extensions",
        "pyharness.agents",
        "pyharness.skills",
        "pyharness.compaction",
        "pyharness.queues",
        "pyharness.events",
        "pyharness.types",
        "pyharness.tools",
        "pyharness.tools.base",
        "pyharness.tools.builtin",
        "pyharness.tools.builtin.read",
        "pyharness.tools.builtin.write",
        "pyharness.tools.builtin.edit",
        "pyharness.tools.builtin.bash",
        "pyharness.tools.builtin.grep",
        "pyharness.tools.builtin.glob_tool",
        "pyharness.tools.builtin.web_search",
        "pyharness.tools.builtin.web_fetch",
    ):
        importlib.import_module(name)
