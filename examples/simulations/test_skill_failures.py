"""Skill failure scenarios — silent suppression in _loader/skills."""

from __future__ import annotations

import textwrap

import pytest

from coding_harness import LoadSkillTool, WorkspaceContext, discover_skills
from pyharness import (
    EventBus,
    ExtensionAPI,
    HandlerContext,
    HookResult,
    LifecycleEvent,
    ToolContext,
    ToolRegistry,
)


def _project(tmp_path):
    home = tmp_path / "home"
    project = home / "p"
    workspace = project / "src"
    workspace.mkdir(parents=True)
    (project / ".pyharness").mkdir()
    return home, workspace


def test_broken_tools_py_skill_still_discovered(tmp_path):
    home, workspace = _project(tmp_path)
    sd = home / "p" / ".pyharness" / "skills" / "demo"
    sd.mkdir(parents=True)
    (sd / "SKILL.md").write_text(
        "---\nname: demo\ndescription: demo skill\n---\nbody",
        encoding="utf-8",
    )
    (sd / "tools.py").write_text("raise RuntimeError('cannot import')\n", encoding="utf-8")

    ctx = WorkspaceContext(workspace=workspace, home=home)
    skills = discover_skills(ctx)
    assert "demo" in skills


@pytest.mark.asyncio
async def test_load_skill_with_broken_tools_returns_partial_success(tmp_path):
    home, workspace = _project(tmp_path)
    sd = home / "p" / ".pyharness" / "skills" / "demo"
    sd.mkdir(parents=True)
    (sd / "SKILL.md").write_text(
        "---\nname: demo\ndescription: demo\n---\nINSTRUCTIONS",
        encoding="utf-8",
    )
    (sd / "tools.py").write_text("raise RuntimeError('cannot import')\n", encoding="utf-8")

    ctx = WorkspaceContext(workspace=workspace, home=home)
    skills = discover_skills(ctx)
    reg = ToolRegistry()
    tool = LoadSkillTool(skills, reg)
    res = await tool.execute(
        tool.args_schema(name="demo"),
        ToolContext(workspace=workspace, session_id="s", run_id="r"),
    )
    assert res.loaded is True
    assert res.tools_added == []
    assert "INSTRUCTIONS" in res.instructions


@pytest.mark.asyncio
async def test_broken_hooks_py_does_not_break_load(tmp_path):
    home, workspace = _project(tmp_path)
    sd = home / "p" / ".pyharness" / "skills" / "bundle"
    sd.mkdir(parents=True)
    (sd / "SKILL.md").write_text(
        "---\nname: bundle\ndescription: bundle\n---\nBODY",
        encoding="utf-8",
    )
    (sd / "hooks.py").write_text(
        "def register(api):\n    raise RuntimeError('hook exploded')\n",
        encoding="utf-8",
    )

    ctx = WorkspaceContext(workspace=workspace, home=home)
    skills = discover_skills(ctx)
    bus = EventBus()
    reg = ToolRegistry()
    api = ExtensionAPI(bus=bus, registry=reg, settings=None)
    tool = LoadSkillTool(skills, reg)
    tool.bind_extension_api(api)

    res = await tool.execute(
        tool.args_schema(name="bundle"),
        ToolContext(workspace=workspace, session_id="s", run_id="r"),
    )
    assert res.loaded is True
    out = await bus.emit(
        LifecycleEvent(name="something"),
        HandlerContext(settings=None, workspace=workspace, session_id="s", run_id="r"),
    )
    assert out.result is HookResult.Continue


def test_skill_dir_without_skill_md_skipped(tmp_path):
    home, workspace = _project(tmp_path)
    (home / "p" / ".pyharness" / "skills" / "ghost").mkdir(parents=True)
    (home / "p" / ".pyharness" / "skills" / "ghost" / "tools.py").write_text(
        "TOOLS = []\n", encoding="utf-8"
    )
    ctx = WorkspaceContext(workspace=workspace, home=home)
    skills = discover_skills(ctx)
    assert "ghost" not in skills


def test_frontmatter_name_overrides_folder(tmp_path):
    home, workspace = _project(tmp_path)
    sd = home / "p" / ".pyharness" / "skills" / "folder-name"
    sd.mkdir(parents=True)
    (sd / "SKILL.md").write_text(
        textwrap.dedent(
            """
            ---
            name: real-name
            description: differs from folder
            ---
            body
            """
        ).strip(),
        encoding="utf-8",
    )
    ctx = WorkspaceContext(workspace=workspace, home=home)
    skills = discover_skills(ctx)
    assert "real-name" in skills
    assert "folder-name" not in skills
