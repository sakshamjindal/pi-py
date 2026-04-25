"""Smoke tests for the pyharness-tui package."""

from __future__ import annotations

import importlib

import pytest

from pyharness import LLMResponse
from harness.coding_agent import CodingAgent


def test_pyharness_tui_imports():
    mod = importlib.import_module("pyharness_tui")
    assert mod.__version__
    assert hasattr(mod, "TuiRenderer")
    assert hasattr(mod, "run_tui")


def test_cli_main_requires_prompt(capsys):
    from pyharness_tui.cli import main

    rc = main([])
    err = capsys.readouterr().err
    assert rc == 2
    assert "no prompt" in err.lower()


def test_run_tui_with_mocked_llm(tmp_path, monkeypatch, isolated_session_dir, capsys):
    """End-to-end: pyharness-tui prints a result panel containing the
    LLM's final text, and exits 0 on completion."""

    real_init = CodingAgent.__init__

    def patched_init(self, config):
        real_init(self, config)

        async def _complete(**_):
            return LLMResponse(text="all done")

        self.llm.complete = _complete  # type: ignore

    monkeypatch.setattr(CodingAgent, "__init__", patched_init)
    monkeypatch.chdir(tmp_path)

    from pyharness_tui.cli import main

    rc = main(["--bare", "do something"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "all done" in out
    assert "result" in out  # the rich panel title
