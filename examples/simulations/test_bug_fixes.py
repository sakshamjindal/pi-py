"""Regression tests for bugs surfaced during the integration-test
campaign and fixed in the same PR.

Each test pins a behaviour that pre-fix would have failed.
"""

from __future__ import annotations

import json

import pytest

from coding_harness import (
    CodingAgent,
    CodingAgentConfig,
    LoadSkillTool,
    Settings,
    WorkspaceContext,
    discover_skills,
)
from pyharness import LLMResponse, ToolContext, ToolRegistry

from ._helpers import install_scripted_llm, make_project

# -----------------------------------------------------------------------
# Bug 1: load_skill called twice on the same skill must dedup, not re-
# inject the body or re-import the module. Pre-fix it returned the body
# every time, wasting tokens and risking duplicate hook registration.
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_double_load_skill_dedups(tmp_path):
    home = tmp_path / "home"
    project = home / "p"
    workspace = project / "src"
    workspace.mkdir(parents=True)
    (project / ".pyharness").mkdir()
    sd = project / ".pyharness" / "skills" / "demo"
    sd.mkdir(parents=True)
    (sd / "SKILL.md").write_text(
        "---\nname: demo\ndescription: demo\n---\nDEMO_BODY",
        encoding="utf-8",
    )
    ctx = WorkspaceContext(workspace=workspace, home=home)
    skills = discover_skills(ctx)
    reg = ToolRegistry()
    tool = LoadSkillTool(skills, reg)

    tctx = ToolContext(workspace=workspace, session_id="s", run_id="r")
    r1 = await tool.execute(tool.args_schema(name="demo"), tctx)
    r2 = await tool.execute(tool.args_schema(name="demo"), tctx)

    # First load returns the body.
    assert r1.loaded is True
    assert "DEMO_BODY" in r1.instructions

    # Second load is idempotent: still loaded=True, but body is NOT
    # re-injected and the message tells the model to proceed.
    assert r2.loaded is True
    assert r2.instructions == ""
    assert r2.tools_added == []
    assert "already loaded" in r2.message.lower()


# -----------------------------------------------------------------------
# Bug 2: corrupt settings.json was silently ignored. Now it warns to
# stderr (and still uses defaults — silent recovery is correct, silent
# *invisibility* is what we're fixing).
# -----------------------------------------------------------------------


def test_corrupt_settings_warns_to_stderr(tmp_path, capsys, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".pyharness").mkdir()
    (project / ".pyharness" / "settings.json").write_text(
        "this is not valid json {",
        encoding="utf-8",
    )

    s = Settings.load(workspace=project, project_root=project, home=home)
    err = capsys.readouterr().err
    # Recovery: defaults still applied.
    assert s.default_model  # not empty
    # Visibility: a clear warning landed on stderr.
    assert "settings.json" in err.lower() or "settings" in err.lower()
    assert str(project / ".pyharness" / "settings.json") in err


def test_settings_non_object_top_level_warns(tmp_path, capsys, monkeypatch):
    """A settings file that's valid JSON but not a top-level object
    (e.g. an array) is also reported, not silently dropped."""

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".pyharness").mkdir()
    (project / ".pyharness" / "settings.json").write_text("[1, 2, 3]", encoding="utf-8")

    Settings.load(workspace=project, project_root=project, home=home)
    err = capsys.readouterr().err
    assert "not a top-level object" in err or "ignoring" in err


# -----------------------------------------------------------------------
# Bug 3: streaming responses had usage=0 across the board for OpenAI-
# compatible providers because LLMClient.stream wasn't passing
# stream_options={"include_usage": True}. This silently broke
# compaction's token-counting trigger and cost reporting.
# -----------------------------------------------------------------------


def test_llm_client_passes_include_usage_in_stream_options():
    """Static check: the kwargs builder includes stream_options. We
    verify by reading the source so we don't need a live LLM."""

    from pathlib import Path

    src = Path(__import__("pyharness").__file__).resolve().parent / "llm.py"
    text = src.read_text(encoding="utf-8")
    # Both the key and the truthy value must be present in the kwargs
    # construction, otherwise OpenRouter / OpenAI streaming usage is empty.
    assert '"stream_options"' in text or "'stream_options'" in text, (
        "stream_options missing from LLMClient kwargs; this regresses "
        "OpenRouter/OpenAI usage reporting"
    )
    assert '"include_usage"' in text or "'include_usage'" in text


# -----------------------------------------------------------------------
# Sanity: existing scripted-LLM scenarios still work. The fix to
# LLMClient.stream cannot regress the mock-mode tests because they
# bypass the live stream entirely.
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scripted_llm_unaffected_by_stream_options_change(tmp_path, isolated_session_dir):
    workspace = make_project(tmp_path)
    agent = CodingAgent(CodingAgentConfig(workspace=workspace, settings=Settings()))
    install_scripted_llm(agent, [LLMResponse(text="ok")])
    result = await agent.run("hi")
    assert result.completed
    assert result.final_output == "ok"
