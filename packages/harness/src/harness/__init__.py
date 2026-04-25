"""harness — coding-agent scaffolding on top of the pyharness SDK.

This package provides the out-of-the-box behaviour that turns the
generic SDK kernel into the ``pyharness`` CLI: settings hierarchy,
AGENTS.md walking, named sub-agents, skills, extensions discovery,
the eight built-in tools, and the command-line entry point.
"""

from __future__ import annotations

from .agents import (
    AgentDefinition,
    discover_agents,
    list_known_tool_names,
    load_agent_definition,
    resolve_tool_list,
)
from .coding_agent import (
    BASE_SYSTEM_PROMPT,
    CodingAgent,
    CodingAgentConfig,
)
from .config import Settings
from .extensions_loader import LoadedExtensions, load_extensions
from .skills import (
    LoadSkillResult,
    LoadSkillTool,
    SkillDefinition,
    build_skill_index,
    discover_skills,
)
from .tools import all_builtin_tools, builtin_registry, builtin_tool_names
from .workspace import WorkspaceContext

__version__ = "0.1.0"

__all__ = [
    "AgentDefinition",
    "BASE_SYSTEM_PROMPT",
    "CodingAgent",
    "CodingAgentConfig",
    "LoadedExtensions",
    "LoadSkillResult",
    "LoadSkillTool",
    "Settings",
    "SkillDefinition",
    "WorkspaceContext",
    "__version__",
    "all_builtin_tools",
    "build_skill_index",
    "builtin_registry",
    "builtin_tool_names",
    "discover_agents",
    "discover_skills",
    "list_known_tool_names",
    "load_agent_definition",
    "load_extensions",
    "resolve_tool_list",
]
