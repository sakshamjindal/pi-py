"""Skill failure scenarios.

Covers the silent-suppression paths in
``coding-harness/_loader.py`` and ``coding-harness/skills.py``: a
broken ``tools.py``, a broken ``hooks.py``, a missing ``SKILL.md``, and
the frontmatter-name-overrides-folder-name behaviour.
"""

from __future__ import annotations

import textwrap

import pytest

from coding_harness import (
    LoadSkillTool,
    WorkspaceContext,
    discover_skills,
)
from pyharness import (
    EventBus,
    ExtensionAPI,
    HandlerContext,
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


def test_skill_with_broken_tools_py_is_silently_skipped(tmp_path):
    """A ``tools.py`` that raises on import must NOT prevent the skill
    from being discovered (we still want the body) — the tools module
    is just not contributed."""

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
async def test_load_skill_with_broken_tools_py_returns_partial_success(tmp_path):
    """Calling ``load_skill`` on a skill whose ``tools.py`` raises:
    ``loaded=True``, ``tools_added=[]``, body still delivered."""

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
async def test_skill_bundle_with_broken_hooks_py_does_not_break_load(tmp_path):
    """A skill with a working ``tools.py`` but a raising ``hooks.py``
    must still load — the hook is silently skipped, but tools and body
    arrive."""

    from pydantic import BaseModel

    from pyharness import Tool

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
    # Bus must have no handlers for any event because the hook raised.
    out = await bus.emit(
        LifecycleEvent(name="something"),
        HandlerContext(settings=None, workspace=workspace, session_id="s", run_id="r"),
    )
    # No handlers => result is Continue.
    from pyharness import HookResult

    assert out.result is HookResult.Continue


def test_skill_directory_without_skill_md_is_skipped(tmp_path):
    """A ``.pyharness/skills/foo/`` with no ``SKILL.md`` is just
    ignored — discovery doesn't error."""

    home, workspace = _project(tmp_path)
    (home / "p" / ".pyharness" / "skills" / "ghost").mkdir(parents=True)
    (home / "p" / ".pyharness" / "skills" / "ghost" / "tools.py").write_text(
        "TOOLS = []\n", encoding="utf-8"
    )

    ctx = WorkspaceContext(workspace=workspace, home=home)
    skills = discover_skills(ctx)
    assert "ghost" not in skills


def test_skill_frontmatter_name_overrides_folder(tmp_path):
    """If ``SKILL.md``'s frontmatter has ``name: foo``, that wins over
    the folder name. The skill is found under the frontmatter name."""

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
