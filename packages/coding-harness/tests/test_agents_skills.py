"""Named agents and skills."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from coding_harness import (
    LoadSkillTool,
    WorkspaceContext,
    build_skill_index,
    discover_agents,
    discover_skills,
    load_agent_definition,
    resolve_tool_list,
)
from pyharness import ToolContext, ToolRegistry


def _setup_project(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    project = home / "p"
    workspace = project / "src"
    workspace.mkdir(parents=True)
    (project / ".pyharness").mkdir(parents=True)
    return home, workspace


def test_load_agent_definition(tmp_path):
    home, _workspace = _setup_project(tmp_path)
    agents_dir = home / "p" / ".pyharness" / "agents"
    agents_dir.mkdir(parents=True)
    md = agents_dir / "ra.md"
    md.write_text(
        textwrap.dedent(
            """
            ---
            name: ra
            description: research agent
            model: fake-model
            tools:
              - read
              - write
            ---

            You are a research agent.
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    agent = load_agent_definition(md)
    assert agent.name == "ra"
    assert agent.tools == ["read", "write"]
    assert "research agent" in agent.body


def test_resolve_tool_list_builtins(tmp_path):
    home, workspace = _setup_project(tmp_path)
    ctx = WorkspaceContext(workspace=workspace, home=home)
    reg = resolve_tool_list(["read", "write", "bash"], ctx)
    assert reg.has("read") and reg.has("write") and reg.has("bash")
    assert not reg.has("edit")


def test_resolve_tool_list_unknown_raises(tmp_path):
    home, workspace = _setup_project(tmp_path)
    ctx = WorkspaceContext(workspace=workspace, home=home)
    with pytest.raises(ValueError):
        resolve_tool_list(["does_not_exist"], ctx, agent_name="x")


def test_discover_skills_and_index(tmp_path):
    home, workspace = _setup_project(tmp_path)
    sd = home / "p" / ".pyharness" / "skills" / "demo"
    sd.mkdir(parents=True)
    (sd / "SKILL.md").write_text(
        textwrap.dedent(
            """
            ---
            name: demo
            description: a demo skill
            tools:
              - demo_tool
            ---

            instructions
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    ctx = WorkspaceContext(workspace=workspace, home=home)
    skills = discover_skills(ctx)
    assert "demo" in skills
    idx = build_skill_index(skills)
    assert "demo" in idx
    assert "load_skill" in idx


@pytest.mark.asyncio
async def test_load_skill_injects_instructions(tmp_path):
    home, workspace = _setup_project(tmp_path)
    sd = home / "p" / ".pyharness" / "skills" / "demo"
    sd.mkdir(parents=True)
    (sd / "SKILL.md").write_text(
        textwrap.dedent(
            """
            ---
            name: demo
            description: demo
            tools: []
            ---

            DO_THE_THING
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    ctx = WorkspaceContext(workspace=workspace, home=home)
    skills = discover_skills(ctx)
    reg = ToolRegistry()
    tool = LoadSkillTool(skills, reg)
    res = await tool.execute(
        tool.args_schema(name="demo"), ToolContext(workspace=workspace, session_id="s", run_id="r")
    )
    assert res.loaded
    assert "DO_THE_THING" in res.instructions
