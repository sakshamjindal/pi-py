"""Hierarchical workspace context.

Three scopes compose: personal (~/.pyharness/), project
(<project>/.pyharness/, discovered by walking up from the workspace), and
workspace (the directory itself). All scope-aware lookups return paths in
"most general first" order (home → project → workspace) so concatenation
"just works" for things like AGENTS.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class WorkspaceContext:
    workspace: Path
    project_root: Path | None = None
    home: Path | None = None

    def __post_init__(self) -> None:
        self.workspace = Path(self.workspace).expanduser().resolve()
        if self.home is None:
            self.home = Path.home()
        else:
            self.home = Path(self.home).expanduser().resolve()
        if self.project_root is None:
            self.project_root = self.discover_project_root()
        elif self.project_root is not None:
            self.project_root = Path(self.project_root).expanduser().resolve()

    # ------------------------------------------------------------------
    # Scope discovery
    # ------------------------------------------------------------------

    def discover_project_root(self) -> Path | None:
        """Walk up from workspace looking for a `.pyharness/` directory.

        Returns the first ancestor (or the workspace itself) that has a
        `.pyharness/` subdirectory. Stops at the home directory so personal
        config never registers as a project root.
        """

        current = self.workspace
        stop = self.home.parent if self.home else None
        while True:
            if current == self.home:
                return None
            if (current / ".pyharness").is_dir():
                return current
            if current.parent == current:
                return None
            if stop is not None and current == stop:
                return None
            current = current.parent

    # ------------------------------------------------------------------
    # AGENTS.md
    # ------------------------------------------------------------------

    def collect_agents_md(self) -> list[tuple[Path, str]]:
        """Collect AGENTS.md files in most-general-first order.

        Returns a list of `(path, content)` pairs ordered home → project →
        workspace so concatenation produces the right precedence.
        """

        results: list[tuple[Path, str]] = []
        seen: set[Path] = set()
        for scope in self._scope_dirs_general_to_specific():
            md = scope / "AGENTS.md"
            if md not in seen and md.is_file():
                try:
                    results.append((md, md.read_text(encoding="utf-8")))
                except OSError:
                    pass
                seen.add(md)
        return results

    # ------------------------------------------------------------------
    # Skills, extensions, tools, agent definitions
    # ------------------------------------------------------------------

    def collect_skills_dirs(self) -> list[Path]:
        return self._collect_subdirs(".pyharness/skills")

    def collect_extensions_dirs(self) -> list[Path]:
        return self._collect_subdirs(".pyharness/extensions")

    def collect_tools_dirs(self) -> list[Path]:
        return self._collect_subdirs(".pyharness/tools")

    def collect_agents_dirs(self) -> list[Path]:
        return self._collect_subdirs(".pyharness/agents")

    def collect_settings_files(self) -> list[Path]:
        """Settings files in most-general-first order. Workspace-level
        settings file is *not* part of this list — only personal and
        project — to match the spec's two-level config hierarchy."""

        files: list[Path] = []
        if self.home is not None:
            personal = self.home / ".pyharness" / "settings.json"
            if personal.is_file():
                files.append(personal)
        if self.project_root is not None:
            project = self.project_root / ".pyharness" / "settings.json"
            if project.is_file() and project != (files[0] if files else None):
                files.append(project)
        return files

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _scope_dirs_general_to_specific(self) -> list[Path]:
        """Return scope directories in most-general-first order.

        For AGENTS.md we want plain directories: the home dir itself, then
        the project root, then the workspace.
        """

        out: list[Path] = []
        if self.home is not None:
            out.append(self.home)
        if self.project_root is not None and self.project_root != self.home:
            out.append(self.project_root)
        if self.workspace != self.project_root and self.workspace != self.home:
            out.append(self.workspace)
        # Deduplicate while preserving order.
        seen: set[Path] = set()
        deduped: list[Path] = []
        for p in out:
            if p not in seen:
                deduped.append(p)
                seen.add(p)
        return deduped

    def _collect_subdirs(self, suffix: str) -> list[Path]:
        """Return `<scope>/<suffix>` directories that exist, general first."""

        results: list[Path] = []
        seen: set[Path] = set()
        bases: list[Path] = []
        if self.home is not None:
            bases.append(self.home)
        if self.project_root is not None and self.project_root != self.home:
            bases.append(self.project_root)
        # Workspace-level extensions/skills/tools/agents are also valid;
        # they sit at <workspace>/.pyharness/<suffix>.
        if self.workspace != self.project_root and self.workspace != self.home:
            bases.append(self.workspace)
        for base in bases:
            d = base / suffix
            if d.is_dir() and d not in seen:
                results.append(d)
                seen.add(d)
        return results

    def render_agents_md(self) -> str:
        """Convenience: concatenate all AGENTS.md content with separators."""

        parts: list[str] = []
        for path, content in self.collect_agents_md():
            parts.append(f"# Guidance from {path}\n\n{content.strip()}")
        return "\n\n".join(parts)
