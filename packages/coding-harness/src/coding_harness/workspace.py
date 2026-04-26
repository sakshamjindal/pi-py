"""Hierarchical workspace context.

One operating directory (``workspace``) with two config scopes:

- **Personal** — ``~/.pyharness/`` (always)
- **Project** — ``<closest ancestor with .pyharness/>/.pyharness/`` (if any)

``project_root`` is just the discovered ancestor — an internal lookup
result, not a separate user-supplied path.

For AGENTS.md, every directory on the path from ``~/`` down to
``workspace`` is scanned, picking up an ``AGENTS.md`` at any level
(matching Claude Code's CLAUDE.md walk). General-first ordering means
the more-specific files override the more-general ones when concatenated.
"""

from __future__ import annotations

import contextlib
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
    # Project root discovery
    # ------------------------------------------------------------------

    def discover_project_root(self) -> Path | None:
        """Walk up from ``workspace`` looking for a ``.pyharness/`` directory.

        Returns the closest ancestor (or the workspace itself) that has
        a ``.pyharness/`` subdirectory. Stops at ``$HOME`` so personal
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
    # AGENTS.md — walks every ancestor of workspace
    # ------------------------------------------------------------------

    def collect_agents_md(self) -> list[tuple[Path, str]]:
        """Collect AGENTS.md files at every directory from home down to
        workspace, general-first.

        Every ``AGENTS.md`` on the path contributes — not just the ones
        at scope boundaries. This matches how Claude Code walks
        ``CLAUDE.md`` and how most repo-aware tools (git, pytest)
        compose hierarchical config.
        """

        results: list[tuple[Path, str]] = []
        seen: set[Path] = set()
        for d in self._ancestor_chain():
            md = d / "AGENTS.md"
            if md not in seen and md.is_file():
                with contextlib.suppress(OSError):
                    results.append((md, md.read_text(encoding="utf-8")))
                seen.add(md)
        return results

    def _ancestor_chain(self) -> list[Path]:
        """Directories from home down to workspace, general-first.

        - Always includes ``~/`` for personal AGENTS.md.
        - Then includes every ancestor of workspace from there down.
        - If workspace lives outside ``$HOME``, ``~/`` is still prepended.
        """

        chain: list[Path] = []
        current = self.workspace
        while True:
            chain.append(current)
            if self.home is not None and current == self.home:
                break
            if current.parent == current:
                break
            current = current.parent
        chain.reverse()
        if self.home is not None and self.home not in chain:
            chain.insert(0, self.home)
        return chain

    # ------------------------------------------------------------------
    # .pyharness/ scope dirs (skills, extensions, tools, agents, settings)
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
        """Settings files in most-general-first order: personal then project."""

        files: list[Path] = []
        if self.home is not None:
            personal = self.home / ".pyharness" / "settings.json"
            if personal.is_file():
                files.append(personal)
        if self.project_root is not None and self.project_root != self.home:
            project = self.project_root / ".pyharness" / "settings.json"
            if project.is_file() and (not files or project != files[0]):
                files.append(project)
        return files

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _collect_subdirs(self, suffix: str) -> list[Path]:
        """Return ``<scope>/<suffix>`` directories that exist, general first.

        Two scopes only: personal (``~``) and project (the discovered
        ancestor with ``.pyharness/``). The workspace is never a scope of
        its own — if you want workspace-local config, put ``.pyharness/``
        in the workspace and it becomes the project root automatically.
        """

        results: list[Path] = []
        seen: set[Path] = set()
        bases: list[Path] = []
        if self.home is not None:
            bases.append(self.home)
        if self.project_root is not None and self.project_root != self.home:
            bases.append(self.project_root)
        for base in bases:
            d = base / suffix
            if d.is_dir() and d not in seen:
                results.append(d)
                seen.add(d)
        return results

    # ------------------------------------------------------------------
    # AGENTS.md rendering — same `@import` deferred-read behavior
    # ------------------------------------------------------------------

    def render_agents_md(self) -> str:
        """Concatenate all AGENTS.md content. Lines starting with ``@`` are
        treated as deferred imports and replaced with a one-line pointer
        instead of being inlined — so big reference docs can live outside
        the system prompt and be read on demand by the agent."""

        parts: list[str] = []
        for path, content in self.collect_agents_md():
            rendered = self._rewrite_imports(path, content)
            parts.append(f"# Guidance from {path}\n\n{rendered.strip()}")
        return "\n\n".join(parts)

    def _rewrite_imports(self, base: Path, content: str) -> str:
        """Replace ``@<path>`` lines with a deferred-read pointer.

        Recognised forms (per line, leading whitespace allowed):
        - ``@path/to/file.md``
        - ``@./relative.md``
        - ``@~/absolute.md``
        Other lines pass through unchanged.
        """

        out: list[str] = []
        for line in content.splitlines():
            stripped = line.lstrip()
            if not stripped.startswith("@"):
                out.append(line)
                continue
            ref = stripped[1:].split(maxsplit=1)[0]
            if not ref or ref.startswith("@"):
                out.append(line)
                continue
            resolved = self._resolve_import(base.parent, ref)
            if resolved is None:
                out.append(line)
                continue
            indent = line[: len(line) - len(stripped)]
            out.append(
                f"{indent}- (Reference document available at `{resolved}` — read it on demand using the `read` tool.)"
            )
        return "\n".join(out)

    def _resolve_import(self, base_dir: Path, ref: str) -> Path | None:
        try:
            ref_path = Path(ref).expanduser()
        except Exception:
            return None
        if not ref_path.is_absolute():
            ref_path = (base_dir / ref_path).resolve()
        else:
            ref_path = ref_path.resolve()
        return ref_path if ref_path.is_file() else None
