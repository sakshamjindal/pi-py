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


def test_collect_agents_md_bounded_at_project_root(tmp_path):
    """AGENTS.md walking is bounded between project_root and workspace.

    Personal ~/AGENTS.md still loads as deliberate global guidance.
    Files BETWEEN home and project_root (e.g. ~/work/AGENTS.md) are
    skipped — they aren't part of this project and would constitute
    home-directory leakage if included.
    """

    home = tmp_path / "home"
    middle = home / "work"
    project = middle / "repo"
    src = project / "src"
    components = src / "components"
    components.mkdir(parents=True)
    (project / ".pyharness").mkdir()

    (home / "AGENTS.md").write_text("home", encoding="utf-8")
    (middle / "AGENTS.md").write_text("middle", encoding="utf-8")  # SKIPPED — above project_root
    (project / "AGENTS.md").write_text("project", encoding="utf-8")
    (src / "AGENTS.md").write_text("src", encoding="utf-8")  # between project & workspace
    (components / "AGENTS.md").write_text("components", encoding="utf-8")

    ctx = WorkspaceContext(workspace=components, home=home)
    contents = [c for _, c in ctx.collect_agents_md()]
    # `middle` is not in the chain — it's above project_root.
    assert contents == ["home", "project", "src", "components"]
    assert "middle" not in contents


def test_collect_agents_md_workspace_outside_home(tmp_path):
    """Workspace outside $HOME still picks up ~/AGENTS.md (personal
    guidance) plus any ancestor AGENTS.md from / down to workspace."""

    home = tmp_path / "home"
    home.mkdir()
    (home / "AGENTS.md").write_text("home", encoding="utf-8")

    workspace = tmp_path / "scratch" / "work"
    workspace.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text("workspace", encoding="utf-8")

    ctx = WorkspaceContext(workspace=workspace, home=home)
    contents = [c for _, c in ctx.collect_agents_md()]
    assert "home" in contents and "workspace" in contents
    # Personal guidance always comes first.
    assert contents[0] == "home"


def test_no_third_workspace_scope_for_pyharness_dirs(tmp_path):
    """A `.pyharness/` at the workspace level (deeper than the project
    root) does NOT count as a separate scope. The closest ancestor with
    `.pyharness/` is the project root, period."""

    home = tmp_path / "home"
    project = home / "repo"
    workspace = project / "src"
    workspace.mkdir(parents=True)

    (project / ".pyharness" / "extensions").mkdir(parents=True)
    # If someone puts a ".pyharness/" at workspace level deeper than the
    # discovered project root, it doesn't contribute a third scope.
    # (In practice it would be picked up by discover_project_root if the
    # walk reached it FIRST — i.e. workspace would BECOME the project
    # root. That's the intended behavior.)
    ctx = WorkspaceContext(workspace=workspace, home=home)
    dirs = ctx.collect_extensions_dirs()
    # Only personal + project; no third entry.
    assert len(dirs) <= 2
    assert all(d != workspace / ".pyharness" / "extensions" for d in dirs)
