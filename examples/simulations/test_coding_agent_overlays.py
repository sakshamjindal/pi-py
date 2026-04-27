"""CodingAgent: project-required, bare bypass, allowlist enforcement,
mid-run-install rejection."""

from __future__ import annotations

import textwrap

import pytest

from coding_harness import CodingAgent, CodingAgentConfig, NoProjectError, Settings
from pyharness import ToolContext

from ._helpers import make_project


def test_no_project_root_raises_actionable_error(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    workspace = tmp_path / "scratch"
    workspace.mkdir()

    with pytest.raises(NoProjectError) as excinfo:
        CodingAgent(CodingAgentConfig(workspace=workspace, settings=Settings()))
    msg = str(excinfo.value)
    assert "pyharness init" in msg
    assert "--bare" in msg
    assert str(workspace) in msg


def test_bare_mode_succeeds_without_marker(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    workspace = tmp_path / "scratch"
    workspace.mkdir()
    agent = CodingAgent(CodingAgentConfig(workspace=workspace, settings=Settings(), bare=True))
    assert agent.workspace_ctx.project_root is None
    assert agent.extensions_loaded == []


@pytest.mark.asyncio
async def test_named_agent_allowlist_blocks_mid_run_skill(tmp_path, monkeypatch):
    """Live discovery must STILL respect the named agent's allowlist.
    A skill installed mid-run that isn't on the agent's `skills:` list
    must be rejected by load_skill — the contract holds."""

    home = tmp_path / "home"
    project = home / "p"
    workspace = project / "src"
    workspace.mkdir(parents=True)
    (project / ".pyharness").mkdir()
    monkeypatch.setenv("HOME", str(home))

    skills_dir = project / ".pyharness" / "skills"
    (skills_dir / "alpha").mkdir(parents=True)
    (skills_dir / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: alpha\n---\nbody",
        encoding="utf-8",
    )
    agents_dir = project / ".pyharness" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "narrow.md").write_text(
        textwrap.dedent(
            """
            ---
            name: narrow
            skills: [alpha]
            ---
            body
            """
        ).strip(),
        encoding="utf-8",
    )

    agent = CodingAgent(
        CodingAgentConfig(
            workspace=workspace, settings=Settings(), agent_name="narrow", project_root=project
        )
    )

    # Drop a new skill mid-run, outside the allowlist.
    (skills_dir / "beta").mkdir(parents=True)
    (skills_dir / "beta" / "SKILL.md").write_text(
        "---\nname: beta\ndescription: beta\n---\nbody",
        encoding="utf-8",
    )

    res = await agent.load_skill_tool.execute(
        agent.load_skill_tool.args_schema(name="beta"),
        ToolContext(workspace=workspace, session_id="s", run_id="r"),
    )
    assert res.loaded is False
    assert "beta" in res.message

    res2 = await agent.load_skill_tool.execute(
        agent.load_skill_tool.args_schema(name="alpha"),
        ToolContext(workspace=workspace, session_id="s", run_id="r"),
    )
    assert res2.loaded is True


@pytest.mark.asyncio
async def test_live_skill_rediscovery_for_default_agent(tmp_path):
    """Default agent (no allowlist) picks up mid-run installs via
    load_skill's live re-discovery."""

    workspace = make_project(tmp_path)
    agent = CodingAgent(CodingAgentConfig(workspace=workspace, settings=Settings(), bare=True))
    sd = workspace / ".pyharness" / "skills" / "late-arrival"
    sd.mkdir(parents=True)
    (sd / "SKILL.md").write_text(
        "---\nname: late-arrival\ndescription: dropped in mid-run\n---\nLATE_BODY",
        encoding="utf-8",
    )
    res = await agent.load_skill_tool.execute(
        agent.load_skill_tool.args_schema(name="late-arrival"),
        ToolContext(workspace=workspace, session_id="s", run_id="r"),
    )
    assert res.loaded is True
    assert "LATE_BODY" in res.instructions


def test_named_agent_tools_keeps_builtins(tmp_path, monkeypatch):
    """`tools: [read]` is additive over builtins, not a replacement."""

    home = tmp_path / "home"
    project = home / "p"
    workspace = project / "src"
    workspace.mkdir(parents=True)
    (project / ".pyharness").mkdir()
    monkeypatch.setenv("HOME", str(home))

    agents_dir = project / ".pyharness" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "minimal.md").write_text(
        textwrap.dedent(
            """
            ---
            name: minimal
            tools: [read]
            ---
            body
            """
        ).strip(),
        encoding="utf-8",
    )
    agent = CodingAgent(
        CodingAgentConfig(
            workspace=workspace, settings=Settings(), agent_name="minimal", project_root=project
        )
    )
    for name in ("read", "write", "edit", "bash", "grep", "glob"):
        assert agent.tool_registry.has(name), f"missing builtin {name}"
