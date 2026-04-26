"""Workspace and AGENTS.md edge cases."""

from __future__ import annotations

import textwrap

from coding_harness import CodingAgent, CodingAgentConfig, Settings, WorkspaceContext


def test_agents_md_at_import_to_nonexistent_passes_through(tmp_path):
    home = tmp_path / "home"
    project = home / "p"
    workspace = project / "src"
    workspace.mkdir(parents=True)
    (project / ".pyharness").mkdir()
    (project / "AGENTS.md").write_text(
        "@nope.md\n\nactual guidance",
        encoding="utf-8",
    )

    ctx = WorkspaceContext(workspace=workspace, home=home)
    rendered = ctx.render_agents_md()
    # Unresolved import line stays intact, not rewritten.
    assert "@nope.md" in rendered
    assert "actual guidance" in rendered


def test_agents_md_deeply_nested_within_project_root(tmp_path):
    home = tmp_path / "home"
    project = home / "p"
    workspace = project / "a" / "b" / "c" / "d"
    workspace.mkdir(parents=True)
    (project / ".pyharness").mkdir()
    (home / "AGENTS.md").write_text("home", encoding="utf-8")
    (project / "AGENTS.md").write_text("project", encoding="utf-8")
    (project / "a" / "AGENTS.md").write_text("a", encoding="utf-8")
    (project / "a" / "b" / "AGENTS.md").write_text("b", encoding="utf-8")
    (project / "a" / "b" / "c" / "AGENTS.md").write_text("c", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("d", encoding="utf-8")

    ctx = WorkspaceContext(workspace=workspace, home=home)
    contents = [c for _, c in ctx.collect_agents_md()]
    # All six picked up, general-first.
    assert contents == ["home", "project", "a", "b", "c", "d"]


def test_bare_mode_skips_project_requirement_via_coding_agent(tmp_path, monkeypatch):
    """A workspace outside any project tree must work with bare=True."""

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()

    workspace = tmp_path / "scratch"
    workspace.mkdir()

    agent = CodingAgent(CodingAgentConfig(workspace=workspace, settings=Settings(), bare=True))
    # Bare mode: no project root, no extensions, AGENTS.md not inlined.
    assert agent.workspace_ctx.project_root is None
    assert agent.extensions_loaded == []
    assert "Guidance from" not in agent.system_prompt


def test_named_agent_body_appears_in_system_prompt(tmp_path, monkeypatch):
    """A named agent's body is appended to the system prompt."""

    home = tmp_path / "home"
    project = home / "p"
    workspace = project / "src"
    workspace.mkdir(parents=True)
    (project / ".pyharness").mkdir()
    monkeypatch.setenv("HOME", str(home))

    agents_dir = project / ".pyharness" / "agents"
    agents_dir.mkdir()
    (agents_dir / "tester.md").write_text(
        textwrap.dedent(
            """
            ---
            name: tester
            ---

            ROLE_BODY_MARKER: you are a tester
            """
        ).strip(),
        encoding="utf-8",
    )

    agent = CodingAgent(
        CodingAgentConfig(
            workspace=workspace,
            settings=Settings(),
            agent_name="tester",
            project_root=project,
        )
    )
    assert "ROLE_BODY_MARKER" in agent.system_prompt
