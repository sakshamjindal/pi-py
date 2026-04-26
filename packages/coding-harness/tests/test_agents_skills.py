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


def test_resolve_tool_list_builtins_always_present(tmp_path):
    """Builtins are always registered. Listing a builtin in ``declared``
    is a no-op; absent builtins are still registered."""

    home, workspace = _setup_project(tmp_path)
    ctx = WorkspaceContext(workspace=workspace, home=home)
    reg = resolve_tool_list(["read", "write", "bash"], ctx)
    # All builtins present, including ones not in `declared`.
    assert reg.has("read") and reg.has("write") and reg.has("bash")
    assert reg.has("edit") and reg.has("grep") and reg.has("glob")


def test_resolve_tool_list_empty_means_builtins(tmp_path):
    home, workspace = _setup_project(tmp_path)
    ctx = WorkspaceContext(workspace=workspace, home=home)
    reg = resolve_tool_list([], ctx)
    assert reg.has("read") and reg.has("edit") and reg.has("bash")


def test_resolve_tool_list_wildcard_means_builtins(tmp_path):
    home, workspace = _setup_project(tmp_path)
    ctx = WorkspaceContext(workspace=workspace, home=home)
    reg = resolve_tool_list(["*"], ctx)
    assert reg.has("read") and reg.has("edit") and reg.has("bash")


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


def test_skill_index_uses_system_reminder(tmp_path):
    home, workspace = _setup_project(tmp_path)
    sd = home / "p" / ".pyharness" / "skills" / "demo"
    sd.mkdir(parents=True)
    (sd / "SKILL.md").write_text(
        "---\nname: demo\ndescription: a demo skill\n---\nbody",
        encoding="utf-8",
    )
    ctx = WorkspaceContext(workspace=workspace, home=home)
    idx = build_skill_index(discover_skills(ctx))
    assert "<system-reminder>" in idx
    assert "</system-reminder>" in idx
    assert "demo" in idx


def test_skill_index_split_when_loaded(tmp_path):
    home, workspace = _setup_project(tmp_path)
    base = home / "p" / ".pyharness" / "skills"
    for n in ("a", "b"):
        (base / n).mkdir(parents=True)
        (base / n / "SKILL.md").write_text(
            f"---\nname: {n}\ndescription: skill {n}\n---\n", encoding="utf-8"
        )
    ctx = WorkspaceContext(workspace=workspace, home=home)
    skills = discover_skills(ctx)
    idx = build_skill_index(skills, loaded={"a"})
    assert "Loaded skills" in idx
    assert "Available skills" in idx
    # 'a' must appear under Loaded, 'b' under Available; loaded names
    # come first in the output.
    pos_a = idx.find("- a")
    pos_b = idx.find("- b: skill b")
    assert pos_a < pos_b


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
    # LoadSkillTool memoizes the loaded names so the index render can show
    # accurate state.
    assert "demo" in tool.loaded_names


@pytest.mark.asyncio
async def test_load_skill_runs_bundle_hooks(tmp_path):
    """A skill bundle (SKILL.md + tools.py + hooks.py) must invoke
    ``hooks.py:register(api)`` when activated, but only if an
    ExtensionAPI has been bound."""

    from pyharness import EventBus, ExtensionAPI, HandlerContext, LifecycleEvent

    home, workspace = _setup_project(tmp_path)
    sd = home / "p" / ".pyharness" / "skills" / "bundle"
    sd.mkdir(parents=True)
    (sd / "SKILL.md").write_text(
        "---\nname: bundle\ndescription: bundle\n---\nbody", encoding="utf-8"
    )
    (sd / "hooks.py").write_text(
        "from pyharness import HookOutcome\n"
        "def register(api):\n"
        "    api.on('e', _h)\n"
        "async def _h(event, ctx):\n"
        "    return HookOutcome.deny('blocked-by-bundle')\n",
        encoding="utf-8",
    )

    ctx = WorkspaceContext(workspace=workspace, home=home)
    skills = discover_skills(ctx)
    assert skills["bundle"].hooks_module is not None

    bus = EventBus()
    reg = ToolRegistry()
    api = ExtensionAPI(bus=bus, registry=reg, settings=None)
    tool = LoadSkillTool(skills, reg)
    tool.bind_extension_api(api)

    await tool.execute(
        tool.args_schema(name="bundle"),
        ToolContext(workspace=workspace, session_id="s", run_id="r"),
    )
    out = await bus.emit(
        LifecycleEvent(name="e"),
        HandlerContext(settings=None, workspace=workspace, session_id="s", run_id="r"),
    )
    assert out.reason == "blocked-by-bundle"
