"""CLI smoke tests."""

from __future__ import annotations

from coding_harness import cli
from coding_harness.coding_agent import CodingAgent
from pyharness import LLMResponse


def test_cli_runs_with_mocked_llm(tmp_path, monkeypatch, isolated_session_dir, capsys):
    real_init = CodingAgent.__init__

    def patched_init(self, config):
        real_init(self, config)

        # Replace the LLM with a one-shot completion that returns "ok".
        async def _complete(**_):
            return LLMResponse(text="ok")

        self.llm.complete = _complete  # type: ignore

    monkeypatch.setattr(CodingAgent, "__init__", patched_init)
    monkeypatch.chdir(tmp_path)

    rc = cli.main(["--bare", "do something"])
    out = capsys.readouterr()
    assert rc == 0
    assert "ok" in out.out


def test_sessions_ls_no_sessions(tmp_path, monkeypatch, capsys, isolated_session_dir):
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["sessions", "ls"])
    err = capsys.readouterr().err
    assert rc == 0
    assert "(no sessions)" in err
