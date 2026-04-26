"""Hierarchical workspace context.

One user-facing input (``workspace``) and one derived bound
(``project_root``) — the closest ancestor with a ``.pyharness/``
marker, found by walking up from ``workspace`` and stopping at
``$HOME``.

Two config scopes for ``.pyharness/<thing>``:

- **Personal** — ``~/.pyharness/`` (always)
- **Project** — ``<project_root>/.pyharness/`` (the discovered marker)

For AGENTS.md, the walk is **bounded at ``project_root``** so home-
adjacent guidance can't leak into unrelated sessions. Personal
``~/AGENTS.md`` still loads (deliberate global guidance), but
intermediate ancestors *between* home and the project are not read.

``CodingAgent`` requires ``project_root`` to be discovered (or
``bare=True``) — running with no marker fails fast and loud rather
than silently using only personal config.
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
    # AGENTS.md — bounded walk between project_root and workspace
    # ------------------------------------------------------------------

    def collect_agents_md(self) -> list[tuple[Path, str]]:
        """Collect AGENTS.md files, general-first.

        Loaded:
        - ``~/AGENTS.md`` (personal, always — deliberate global guidance)
        - Every directory from ``project_root`` down to ``workspace``
          (inclusive both ends)

        Skipped:
        - Any ``AGENTS.md`` *between* ``$HOME`` and ``project_root``.
          That guidance isn't part of this project; including it would
          be the home-config-leakage failure mode.

        If ``project_root`` is ``None`` (only valid under bare mode),
        only ``~/AGENTS.md`` and ``<workspace>/AGENTS.md`` are read.
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
        """Directories to scan for AGENTS.md, general-first.

        Personal ``~/`` is always prepended. Then, if a project root
        was discovered, every directory from ``project_root`` down to
        ``workspace``. If no project root (bare mode), just the
        workspace itself.
        """

        chain: list[Path] = []

        if self.project_root is not None:
            current = self.workspace
            while True:
                chain.append(current)
                if current == self.project_root:
                    break
                if current.parent == current:
                    break
                current = current.parent
            chain.reverse()  # general-first
        else:
            chain.append(self.workspace)

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
