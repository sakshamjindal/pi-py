"""Tests for the minimal pyharness-tui REPL."""

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

    (tmp_path / ".pyharness").mkdir()

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


def test_no_project_in_one_shot_prints_friendly_error(tmp_path, monkeypatch, capsys):
    """Running pyharness-tui in a fresh dir without --bare must NOT
    crash with NoProjectError traceback. It should print the message
    to stderr and exit with rc=2."""

    monkeypatch.setenv("HOME", str(tmp_path / "fakehome"))
    (tmp_path / "fakehome").mkdir()
    monkeypatch.chdir(tmp_path)

    from pyharness_tui.cli import main

    rc = main(["any prompt"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "No project found" in err
    assert "pyharness init" in err
    assert "--bare" in err


def test_bare_flag_bypasses_project_one_shot(tmp_path, monkeypatch, isolated_session_dir, capsys):
    """`pyharness-tui --bare prompt` must succeed without a marker."""

    (tmp_path / "fakehome").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "fakehome"))
    monkeypatch.chdir(tmp_path)

    real_init = CodingAgent.__init__

    def patched_init(self, config):
        real_init(self, config)

        async def _complete(**_):
            return LLMResponse(text="bare-ok")

        self.llm.complete = _complete  # type: ignore

    monkeypatch.setattr(CodingAgent, "__init__", patched_init)

    from pyharness_tui.cli import main

    rc = main(["--bare", "say hi"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "bare-ok" in out


def test_workspace_flag_overrides_cwd(tmp_path, monkeypatch, isolated_session_dir, capsys):
    """`--workspace <dir>` operates in that dir, not cwd."""

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / ".pyharness").mkdir()

    real_init = CodingAgent.__init__
    seen_workspaces: list = []

    def patched_init(self, config):
        seen_workspaces.append(config.workspace)
        real_init(self, config)

        async def _complete(**_):
            return LLMResponse(text="ok")

        self.llm.complete = _complete  # type: ignore

    monkeypatch.setattr(CodingAgent, "__init__", patched_init)
    monkeypatch.chdir(tmp_path)  # cwd is NOT a project; --workspace is

    from pyharness_tui.cli import main

    rc = main(["--workspace", str(elsewhere), "go"])
    assert rc == 0
    assert seen_workspaces and str(seen_workspaces[0]).startswith(str(elsewhere))


def test_repl_exit_command(tmp_path, monkeypatch, isolated_session_dir):
    """Typing `exit` (or `quit`) at the REPL prompt cleanly exits."""

    (tmp_path / ".pyharness").mkdir()
    monkeypatch.chdir(tmp_path)

    inputs = iter(["exit"])

    def _input(_prompt):
        return next(inputs)

    monkeypatch.setattr("builtins.input", _input)

    from pyharness_tui.cli import main

    rc = main([])
    assert rc == 0


def test_repl_quit_command(tmp_path, monkeypatch, isolated_session_dir):
    (tmp_path / ".pyharness").mkdir()
    monkeypatch.chdir(tmp_path)

    inputs = iter(["quit"])

    def _input(_prompt):
        return next(inputs)

    monkeypatch.setattr("builtins.input", _input)

    from pyharness_tui.cli import main

    rc = main([])
    assert rc == 0


def test_repl_blank_line_continues(tmp_path, monkeypatch, isolated_session_dir):
    """Empty input doesn't run an agent — just re-prompts."""

    (tmp_path / ".pyharness").mkdir()
    monkeypatch.chdir(tmp_path)

    inputs = iter(["", "  ", "exit"])

    def _input(_prompt):
        return next(inputs)

    monkeypatch.setattr("builtins.input", _input)

    from pyharness_tui.cli import main

    rc = main([])
    assert rc == 0  # no agent ever constructed; the test passes if main returns


def test_startup_banner_shows_workspace_and_model(
    tmp_path, monkeypatch, isolated_session_dir, capsys
):
    """The REPL prints a banner with workspace + model so the user knows
    what they're running against."""

    (tmp_path / ".pyharness").mkdir()
    monkeypatch.chdir(tmp_path)

    inputs = iter(["exit"])

    def _input(_prompt):
        return next(inputs)

    monkeypatch.setattr("builtins.input", _input)

    from pyharness_tui.cli import main

    main(["--model", "test-model"])
    err = capsys.readouterr().err
    assert "workspace=" in err
    assert "test-model" in err


def test_repl_carries_session_across_prompts(tmp_path, monkeypatch, isolated_session_dir, capsys):
    """REGRESSION: REPL must keep the model's prior context across
    prompts. Pre-fix, every prompt got a fresh session, producing
    multiple JSONL files with no shared history. Now the second prompt
    resumes the first prompt's session_id, so all turns land in one log
    and the model sees prior messages on subsequent calls.
    """

    (tmp_path / ".pyharness").mkdir()
    monkeypatch.chdir(tmp_path)

    inputs = iter(["first prompt", "second prompt", "exit"])

    def _input(_prompt):
        return next(inputs)

    monkeypatch.setattr("builtins.input", _input)

    seen_session_ids: list[str] = []
    real_init = CodingAgent.__init__

    def patched_init(self, config):
        real_init(self, config)
        seen_session_ids.append(self.session.session_id)

        async def _complete(**_):
            return LLMResponse(text="ok")

        self.llm.complete = _complete  # type: ignore

    monkeypatch.setattr(CodingAgent, "__init__", patched_init)

    from pyharness_tui.cli import main

    main([])

    # Both prompts produced agents — and they SHARE the same session_id.
    assert len(seen_session_ids) == 2, f"expected 2 prompts to build agents, got {seen_session_ids}"
    assert seen_session_ids[0] == seen_session_ids[1], (
        f"REPL fragmented the conversation: turn 1 session={seen_session_ids[0]}, "
        f"turn 2 session={seen_session_ids[1]}. Should be the same id (resume)."
    )

    # Exactly one JSONL file on disk (not one per prompt).
    log_files = list((isolated_session_dir).rglob("*.jsonl"))
    assert len(log_files) == 1, (
        f"expected 1 session file across both prompts, got {len(log_files)}: {log_files}"
    )
