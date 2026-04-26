"""CodingAgent: allowlist resolution, programmatic overlays, project-required."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from coding_harness import (
    CodingAgent,
    CodingAgentConfig,
    NoProjectError,
    Settings,
    SkillDefinition,
)


def _setup_workspace_with_extensions(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    project = home / "p"
    workspace = project / "src"
    workspace.mkdir(parents=True)
    (project / ".pyharness" / "extensions").mkdir(parents=True)
    (project / ".pyharness" / "extensions" / "always_on.py").write_text(
        "from pyharness import HookOutcome\n"
        "def register(api):\n"
        "    api.on('e', _h)\n"
        "async def _h(event, ctx):\n"
        "    return HookOutcome.deny('was_active')\n",
        encoding="utf-8",
    )
    return home, workspace


def _basic_config(workspace: Path, **kwargs) -> CodingAgentConfig:
    return CodingAgentConfig(
        workspace=workspace,
        settings=Settings(),
        **kwargs,
    )


def test_extensions_not_auto_loaded_for_default_agent(tmp_path, monkeypatch):
    """No --agent and no extensions_enabled => no extensions activated,
    even if they exist in scope."""

    home, workspace = _setup_workspace_with_extensions(tmp_path)
    monkeypatch.setenv("HOME", str(home))

    agent = CodingAgent(_basic_config(workspace, project_root=home / "p"))

    # The extension must be discoverable but NOT activated.
    assert "always_on" in agent.extensions_available.refs
    assert agent.extensions_loaded == []


def test_extensions_loaded_when_explicit_in_config(tmp_path, monkeypatch):
    home, workspace = _setup_workspace_with_extensions(tmp_path)
    monkeypatch.setenv("HOME", str(home))

    agent = CodingAgent(
        _basic_config(
            workspace,
            project_root=home / "p",
            extensions_enabled=["always_on"],
        )
    )
    assert "always_on" in agent.extensions_loaded


def test_named_agent_extensions_frontmatter_activates(tmp_path, monkeypatch):
    home, workspace = _setup_workspace_with_extensions(tmp_path)
    monkeypatch.setenv("HOME", str(home))

    agents_dir = home / "p" / ".pyharness" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "ra.md").write_text(
        textwrap.dedent(
            """
            ---
            name: ra
            extensions:
              - always_on
            ---
            body
            """
        ).strip(),
        encoding="utf-8",
    )

    agent = CodingAgent(_basic_config(workspace, project_root=home / "p", agent_name="ra"))
    assert "always_on" in agent.extensions_loaded


def test_extra_skills_overlay(tmp_path, monkeypatch):
    home, workspace = _setup_workspace_with_extensions(tmp_path)
    monkeypatch.setenv("HOME", str(home))

    in_memory_skill = SkillDefinition(
        name="in-memory-skill",
        description="Programmatically registered skill",
        body="instructions",
    )
    agent = CodingAgent(
        _basic_config(
            workspace,
            project_root=home / "p",
            extra_skills=[in_memory_skill],
        )
    )
    assert "in-memory-skill" in agent.skills


def test_skills_allowlist_filters_visible_skills(tmp_path, monkeypatch):
    home, workspace = _setup_workspace_with_extensions(tmp_path)
    monkeypatch.setenv("HOME", str(home))

    skills_dir = home / "p" / ".pyharness" / "skills"
    for n in ("alpha", "beta"):
        (skills_dir / n).mkdir(parents=True)
        (skills_dir / n / "SKILL.md").write_text(
            f"---\nname: {n}\ndescription: skill {n}\n---\nbody",
            encoding="utf-8",
        )

    agents_dir = home / "p" / ".pyharness" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "narrow.md").write_text(
        textwrap.dedent(
            """
            ---
            name: narrow
            skills:
              - alpha
            ---
            body
            """
        ).strip(),
        encoding="utf-8",
    )

    agent = CodingAgent(_basic_config(workspace, project_root=home / "p", agent_name="narrow"))
    assert "alpha" in agent.skills
    assert "beta" not in agent.skills


def test_skills_wildcard_means_all(tmp_path, monkeypatch):
    home, workspace = _setup_workspace_with_extensions(tmp_path)
    monkeypatch.setenv("HOME", str(home))

    skills_dir = home / "p" / ".pyharness" / "skills"
    for n in ("alpha", "beta"):
        (skills_dir / n).mkdir(parents=True)
        (skills_dir / n / "SKILL.md").write_text(
            f"---\nname: {n}\ndescription: skill {n}\n---\nbody",
            encoding="utf-8",
        )

    agents_dir = home / "p" / ".pyharness" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "wide.md").write_text(
        textwrap.dedent(
            """
            ---
            name: wide
            skills:
              - "*"
            ---
            body
            """
        ).strip(),
        encoding="utf-8",
    )

    agent = CodingAgent(_basic_config(workspace, project_root=home / "p", agent_name="wide"))
    assert "alpha" in agent.skills and "beta" in agent.skills


def test_extra_extensions_register_fn_runs(tmp_path, monkeypatch):
    home, workspace = _setup_workspace_with_extensions(tmp_path)
    monkeypatch.setenv("HOME", str(home))

    captured: list[str] = []

    def my_register(api):
        captured.append("ran")

    CodingAgent(
        _basic_config(
            workspace,
            project_root=home / "p",
            extra_extensions=[my_register],
        )
    )
    assert captured == ["ran"]


def test_no_project_root_raises(tmp_path, monkeypatch):
    """Non-bare CodingAgent without a discoverable .pyharness/ marker
    must fail fast with NoProjectError, not silently proceed."""

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    workspace = tmp_path / "scratch"
    workspace.mkdir()

    with pytest.raises(NoProjectError) as excinfo:
        CodingAgent(CodingAgentConfig(workspace=workspace, settings=Settings()))
    msg = str(excinfo.value)
    # Error must be actionable.
    assert "pyharness init" in msg
    assert "--bare" in msg
    assert str(workspace) in msg


def test_no_project_root_bare_mode_succeeds(tmp_path, monkeypatch):
    """``bare=True`` skips the project requirement."""

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    workspace = tmp_path / "scratch"
    workspace.mkdir()

    agent = CodingAgent(CodingAgentConfig(workspace=workspace, settings=Settings(), bare=True))
    assert agent.workspace_ctx.project_root is None
    # No extensions activated, no AGENTS.md inlined.
    assert agent.extensions_loaded == []


@pytest.mark.asyncio
async def test_named_agent_allowlist_blocks_mid_run_skill(tmp_path, monkeypatch):
    """Live discovery must still respect the named agent's allowlist.

    A skill installed mid-run that isn't on the agent's `skills:` list
    must still be rejected by `load_skill`. The contract holds.
    """

    from pyharness import ToolContext

    home, workspace = _setup_workspace_with_extensions(tmp_path)
    monkeypatch.setenv("HOME", str(home))

    skills_dir = home / "p" / ".pyharness" / "skills"
    (skills_dir / "alpha").mkdir(parents=True)
    (skills_dir / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: alpha\n---\nbody",
        encoding="utf-8",
    )

    agents_dir = home / "p" / ".pyharness" / "agents"
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

    agent = CodingAgent(_basic_config(workspace, project_root=home / "p", agent_name="narrow"))

    # Drop a new skill mid-run — outside the allowlist.
    (skills_dir / "beta").mkdir(parents=True)
    (skills_dir / "beta" / "SKILL.md").write_text(
        "---\nname: beta\ndescription: beta\n---\nbody",
        encoding="utf-8",
    )

    res = await agent.load_skill_tool.execute(
        agent.load_skill_tool.args_schema(name="beta"),
        ToolContext(workspace=workspace, session_id="s", run_id="r"),
    )
    # Live discovery sees beta on disk, but the allowlist filters it out.
    assert res.loaded is False
    assert "beta" in res.message  # error message mentions the rejected name

    # Alpha (on the allowlist) is still loadable.
    res2 = await agent.load_skill_tool.execute(
        agent.load_skill_tool.args_schema(name="alpha"),
        ToolContext(workspace=workspace, session_id="s", run_id="r"),
    )
    assert res2.loaded is True


@pytest.mark.asyncio
async def test_named_agent_tools_frontmatter_keeps_builtins(tmp_path, monkeypatch):
    """Listing a small tools list in frontmatter must not strip builtins."""

    home, workspace = _setup_workspace_with_extensions(tmp_path)
    monkeypatch.setenv("HOME", str(home))

    agents_dir = home / "p" / ".pyharness" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "minimal.md").write_text(
        textwrap.dedent(
            """
            ---
            name: minimal
            tools:
              - read
            ---
            body
            """
        ).strip(),
        encoding="utf-8",
    )

    agent = CodingAgent(_basic_config(workspace, project_root=home / "p", agent_name="minimal"))
    # All builtins must be present even though only `read` is listed.
    for name in ("read", "write", "edit", "bash", "grep", "glob"):
        assert agent.tool_registry.has(name), f"missing builtin {name}"
