"""Smoke test: both packages import and the CLI is registered."""

from __future__ import annotations

import importlib


def test_pyharness_sdk_imports():
    pkg = importlib.import_module("pyharness")
    assert pkg.__version__
    assert hasattr(pkg, "Agent")
    assert hasattr(pkg, "AgentOptions")


def test_coding_harness_package_imports():
    pkg = importlib.import_module("coding_harness")
    assert pkg.__version__
    assert hasattr(pkg, "CodingAgent")
    assert hasattr(pkg, "Settings")


def test_cli_main_requires_prompt(capsys):
    from coding_harness.cli import main

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
        # Coding-harness scaffolding.
        "coding_harness",
        "coding_harness.cli",
        "coding_harness.coding_agent",
        "coding_harness.config",
        "coding_harness.workspace",
        "coding_harness.agents",
        "coding_harness.skills",
        "coding_harness.extensions_loader",
        "coding_harness._loader",
        "coding_harness.tools",
        "coding_harness.tools.builtin",
        "coding_harness.tools.builtin.read",
        "coding_harness.tools.builtin.write",
        "coding_harness.tools.builtin.edit",
        "coding_harness.tools.builtin.bash",
        "coding_harness.tools.builtin.grep",
        "coding_harness.tools.builtin.glob_tool",
        "coding_harness.tools.builtin.web_search",
        "coding_harness.tools.builtin.web_fetch",
    ):
        importlib.import_module(name)
