"""WorkspaceContext: AGENTS.md walking, @import support, scope discovery."""

from __future__ import annotations

from pathlib import Path

import textwrap

from coding_harness import WorkspaceContext


def test_render_agents_md_at_import_is_not_inlined(tmp_path):
    home = tmp_path / "home"
    project = home / "p"
    workspace = project / "src"
    workspace.mkdir(parents=True)
    (project / ".pyharness").mkdir()

    big = project / "BIG_REFERENCE.md"
    big.write_text("This file is huge, do not inline it.", encoding="utf-8")

    (project / "AGENTS.md").write_text(
        textwrap.dedent(
            """
            Top-level guidance.

            @BIG_REFERENCE.md

            More guidance after the import.
            """
        ).strip(),
        encoding="utf-8",
    )

    ctx = WorkspaceContext(workspace=workspace, home=home)
    rendered = ctx.render_agents_md()
    assert "Top-level guidance" in rendered
    assert "More guidance after the import" in rendered
    # The big file's content must not appear inline.
    assert "huge" not in rendered
    # But its path must be advertised as readable.
    assert "BIG_REFERENCE.md" in rendered
    assert "read" in rendered  # tells the agent how to access it


def test_render_agents_md_at_import_unresolved_passes_through(tmp_path):
    home = tmp_path / "home"
    project = home / "p"
    workspace = project / "src"
    workspace.mkdir(parents=True)
    (project / ".pyharness").mkdir()
    (project / "AGENTS.md").write_text("@does_not_exist.md", encoding="utf-8")

    ctx = WorkspaceContext(workspace=workspace, home=home)
    rendered = ctx.render_agents_md()
    # Unresolved imports stay as-is so the user can see what's broken.
    assert "@does_not_exist.md" in rendered


def test_discover_project_root(tmp_path):
    project = tmp_path / "project"
    nested = project / "deep" / "deeper"
    nested.mkdir(parents=True)
    (project / ".pyharness").mkdir()
    ctx = WorkspaceContext(workspace=nested, home=tmp_path)
    assert ctx.project_root == project


def test_collect_agents_md_general_first(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "home" / "project"
    workspace = project / "src"
    workspace.mkdir(parents=True)
    (home / "AGENTS.md").write_text("home guidance", encoding="utf-8")
    (project / "AGENTS.md").write_text("project guidance", encoding="utf-8")
    (project / ".pyharness").mkdir()
    (workspace / "AGENTS.md").write_text("workspace guidance", encoding="utf-8")

    ctx = WorkspaceContext(workspace=workspace, home=home)
    files = ctx.collect_agents_md()
    contents = [c for _, c in files]
    assert contents[0] == "home guidance"
    assert contents[-1] == "workspace guidance"
    assert "project guidance" in contents


def test_collect_extensions_dirs(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "home" / "project"
    workspace = project / "src"
    workspace.mkdir(parents=True)
    (home / ".pyharness" / "extensions").mkdir(parents=True)
    (project / ".pyharness" / "extensions").mkdir(parents=True)

    ctx = WorkspaceContext(workspace=workspace, home=home)
    dirs = ctx.collect_extensions_dirs()
    assert dirs[0] == home / ".pyharness" / "extensions"
    assert dirs[-1] == project / ".pyharness" / "extensions"
