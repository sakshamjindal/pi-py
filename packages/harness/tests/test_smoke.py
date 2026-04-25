"""Smoke test: both packages import and the CLI is registered."""

from __future__ import annotations

import importlib


def test_pyharness_sdk_imports():
    pkg = importlib.import_module("pyharness")
    assert pkg.__version__
    assert hasattr(pkg, "Agent")
    assert hasattr(pkg, "AgentOptions")


def test_harness_package_imports():
    pkg = importlib.import_module("harness")
    assert pkg.__version__
    assert hasattr(pkg, "CodingAgent")
    assert hasattr(pkg, "Settings")


def test_cli_main_requires_prompt(capsys):
    from harness.cli import main

    rc = main([])
    captured = capsys.readouterr()
    assert rc == 2
    assert "no prompt" in captured.err.lower()


def test_subsystem_modules_import():
    for name in (
        # SDK kernel.
        "pyharness",
        "pyharness.loop",
        "pyharness.llm",
        "pyharness.session",
        "pyharness.queues",
        "pyharness.events",
        "pyharness.compaction",
        "pyharness.extensions",
        "pyharness.types",
        "pyharness.tools",
        "pyharness.tools.base",
        # Harness scaffolding.
        "harness",
        "harness.cli",
        "harness.coding_agent",
        "harness.config",
        "harness.workspace",
        "harness.agents",
        "harness.skills",
        "harness.extensions_loader",
        "harness._loader",
        "harness.tools",
        "harness.tools.builtin",
        "harness.tools.builtin.read",
        "harness.tools.builtin.write",
        "harness.tools.builtin.edit",
        "harness.tools.builtin.bash",
        "harness.tools.builtin.grep",
        "harness.tools.builtin.glob_tool",
        "harness.tools.builtin.web_search",
        "harness.tools.builtin.web_fetch",
    ):
        importlib.import_module(name)
