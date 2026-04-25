"""Named agent definitions: frontmatter parsing, tool resolution.

A named agent is a Markdown file with YAML frontmatter at
``<scope>/.pyharness/agents/<name>.md``. The frontmatter declares the
agent's identity (name, description), default model, the tools available
to it (resolved against builtins, project tools, and skills), and an
optional default workdir. The body is the system prompt.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from pyharness import Tool, ToolRegistry

from ._loader import load_tools_from_module
from .tools.builtin import all_builtin_tools, builtin_tool_names
from .workspace import WorkspaceContext


class AgentDefinition(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    description: str = ""
    model: str | None = None
    tools: list[str] = Field(default_factory=list)
    workdir: str | None = None
    body: str = ""
    raw_frontmatter: dict[str, Any] = Field(default_factory=dict)
    source_path: str | None = None


def load_agent_definition(path: Path) -> AgentDefinition:
    import frontmatter  # python-frontmatter

    post = frontmatter.load(str(path))
    fm = dict(post.metadata or {})
    return AgentDefinition(
        name=str(fm.get("name") or path.stem),
        description=str(fm.get("description") or ""),
        model=fm.get("model"),
        tools=list(fm.get("tools") or []),
        workdir=fm.get("workdir"),
        body=post.content or "",
        raw_frontmatter=fm,
        source_path=str(path),
    )


def discover_agents(workspace: WorkspaceContext) -> dict[str, Path]:
    """Walk agents directories. Project entries override personal on
    name collision because project dirs come last in our scope order."""

    found: dict[str, Path] = {}
    for d in workspace.collect_agents_dirs():
        if not d.is_dir():
            continue
        for entry in sorted(d.iterdir()):
            if entry.is_file() and entry.suffix == ".md":
                found[entry.stem] = entry
    return found


# ---------------------------------------------------------------------------
# Tool resolution
# ---------------------------------------------------------------------------


def _collect_project_tools(workspace: WorkspaceContext) -> dict[str, Tool]:
    """Collect tools from `.pyharness/tools/` modules across scopes."""

    out: dict[str, Tool] = {}
    for d in workspace.collect_tools_dirs():
        if not d.is_dir():
            continue
        for entry in sorted(d.iterdir()):
            if (entry.is_file() and entry.suffix == ".py") or (
                entry.is_dir() and (entry / "__init__.py").is_file()
            ):
                for tool in load_tools_from_module(entry):
                    out[tool.name] = tool
    return out


def _collect_skill_tools(workspace: WorkspaceContext) -> dict[str, Tool]:
    """Collect tools from skill modules so frontmatter can reference them
    as always-on without loading the skill body."""

    from .skills import discover_skills  # local import to avoid cycle

    out: dict[str, Tool] = {}
    for skill in discover_skills(workspace).values():
        if skill.tools_module is None:
            continue
        for tool in load_tools_from_module(skill.tools_module):
            out[tool.name] = tool
    return out


def resolve_tool_list(
    declared: list[str],
    workspace: WorkspaceContext,
    *,
    agent_name: str | None = None,
) -> ToolRegistry:
    """Build a registry containing only the declared tools (or all builtins
    when ``declared`` is empty)."""

    builtins = {t.name: t for t in all_builtin_tools()}
    if not declared:
        reg = ToolRegistry()
        for t in builtins.values():
            reg.register(t)
        return reg

    project_tools = _collect_project_tools(workspace)
    skill_tools = _collect_skill_tools(workspace)

    reg = ToolRegistry()
    for name in declared:
        if name in builtins:
            reg.register(builtins[name])
        elif name in project_tools:
            reg.register(project_tools[name])
        elif name in skill_tools:
            reg.register(skill_tools[name])
        else:
            raise ValueError(
                f"Agent {agent_name or '?'} declares tool {name!r} but it was "
                f"not found in builtins, project tools, or skills."
            )
    return reg


def list_known_tool_names(workspace: WorkspaceContext) -> list[str]:
    names = set(builtin_tool_names())
    names.update(_collect_project_tools(workspace))
    names.update(_collect_skill_tools(workspace))
    return sorted(names)
