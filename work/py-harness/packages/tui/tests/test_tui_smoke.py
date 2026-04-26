"""Smoke tests for the minimal pyharness-tui REPL."""

from __future__ import annotations

import importlib

from coding_harness.coding_agent import CodingAgent
from pyharness import LLMResponse


def test_pyharness_tui_imports():
    mod = importlib.import_module("pyharness_tui")
    assert mod.__version__
    assert callable(mod.main)


def test_one_shot_with_mocked_llm(tmp_path, monkeypatch, isolated_session_dir, capsys):
    """`pyharness-tui "prompt"` runs once and prints the LLM's final text."""

    real_init = CodingAgent.__init__

    def patched_init(self, config):
        real_init(self, config)

        async def _complete(**_):
            return LLMResponse(text="all done")

        self.llm.complete = _complete  # type: ignore

    monkeypatch.setattr(CodingAgent, "__init__", patched_init)
    monkeypatch.chdir(tmp_path)

    from pyharness_tui.cli import main

    rc = main(["do", "something"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "all done" in out


def test_repl_exits_on_eof(tmp_path, monkeypatch, isolated_session_dir):
    """No-arg invocation enters the REPL; immediate EOF exits cleanly."""

    monkeypatch.chdir(tmp_path)

    def _eof(_prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)

    from pyharness_tui.cli import main

    rc = main([])
    assert rc == 0
