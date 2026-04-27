"""Tests for the minimal pyharness-tui REPL."""

from __future__ import annotations

import importlib
import os

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


def test_main_loads_dotenv_before_agent_construction(tmp_path, monkeypatch, isolated_session_dir):
    """Regression: pyharness-tui must auto-load .env files (mirroring the
    pyharness CLI) so that a fresh terminal without shell exports picks up
    API keys before the first LLM call."""

    (tmp_path / ".pyharness").mkdir()
    (tmp_path / ".env").write_text(
        "PI_TUI_DOTENV_TEST=loaded-from-workspace-env\n", encoding="utf-8"
    )

    monkeypatch.delenv("PI_TUI_DOTENV_TEST", raising=False)
    monkeypatch.setenv("PYHARNESS_HOME", str(tmp_path / "nonexistent-home"))

    real_init = CodingAgent.__init__

    def patched_init(self, config):
        # By the time the agent is being constructed, load_env must
        # already have populated the env var from the workspace .env.
        assert os.environ.get("PI_TUI_DOTENV_TEST") == "loaded-from-workspace-env"
        real_init(self, config)

        async def _complete(**_):
            return LLMResponse(text="dotenv-ok")

        self.llm.complete = _complete  # type: ignore

    monkeypatch.setattr(CodingAgent, "__init__", patched_init)
    monkeypatch.chdir(tmp_path)

    from pyharness_tui.cli import main

    rc = main(["do", "x"])
    assert rc == 0


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


# ---------------------------------------------------------------------------
# _format_tool_trace
# ---------------------------------------------------------------------------


def test_format_tool_trace_bare_tool_name_when_no_args():
    from pyharness_tui.cli import _format_tool_trace

    assert _format_tool_trace("read", None) == "  → read"
    assert _format_tool_trace("read", {}) == "  → read"


def test_format_tool_trace_uses_per_tool_preview_key():
    """Each well-known tool has a designated 'most informative' argument
    that gets shown after the tool name. Read uses path; bash uses
    command; grep uses pattern."""

    from pyharness_tui.cli import _format_tool_trace

    assert _format_tool_trace("read", {"path": "config.json"}) == "  → read config.json"
    assert _format_tool_trace("bash", {"command": "ls -la"}) == "  → bash ls -la"
    assert _format_tool_trace("grep", {"pattern": "TODO"}) == "  → grep TODO"
    assert _format_tool_trace("web_fetch", {"url": "https://x"}) == "  → web_fetch https://x"


def test_format_tool_trace_falls_back_to_first_scalar_for_unknown_tool():
    """Tools without explicit preview rules show the first scalar
    argument so the user still sees *something* useful."""

    from pyharness_tui.cli import _format_tool_trace

    line = _format_tool_trace("custom_tool", {"target": "thing", "options": {"x": 1}})
    assert line == "  → custom_tool thing"


def test_format_tool_trace_truncates_long_previews():
    """Multi-line bash commands or huge URLs would otherwise blow up
    one TUI line into many. Keep the trace one line and bounded."""

    from pyharness_tui.cli import _format_tool_trace

    long_cmd = "echo " + ("x" * 500)
    line = _format_tool_trace("bash", {"command": long_cmd})
    # One line, well under (length + ellipsis tolerance).
    assert "\n" not in line
    assert len(line) < 120


def test_format_tool_trace_collapses_whitespace():
    """Newlines/tabs in tool arguments must not break the trace line."""

    from pyharness_tui.cli import _format_tool_trace

    line = _format_tool_trace("bash", {"command": "echo hello\n\tworld\n  &&  ls"})
    assert "\n" not in line
    assert "echo hello world && ls" in line
