"""pyharness — a minimal Python agent harness.

Public SDK surface. Stable across the v1 line.
"""

from __future__ import annotations

from .config import Settings
from .extensions import EventBus, ExtensionAPI, HookOutcome, HookResult
from .harness import Harness, HarnessConfig
from .queues import HarnessHandle, MessageQueue
from .session import Session, SessionInfo
from .skills import SkillDefinition, discover_skills
from .agents import AgentDefinition, discover_agents
from .tools import (
    Tool,
    ToolContext,
    ToolError,
    ToolRegistry,
    all_builtin_tools,
    builtin_registry,
)
from .types import LLMResponse, Message, RunResult, ToolCall, TokenUsage
from .workspace import WorkspaceContext

__version__ = "0.1.0"

__all__ = [
    "AgentDefinition",
    "EventBus",
    "ExtensionAPI",
    "Harness",
    "HarnessConfig",
    "HarnessHandle",
    "HookOutcome",
    "HookResult",
    "LLMResponse",
    "Message",
    "MessageQueue",
    "RunResult",
    "Session",
    "SessionInfo",
    "Settings",
    "SkillDefinition",
    "TokenUsage",
    "Tool",
    "ToolCall",
    "ToolContext",
    "ToolError",
    "ToolRegistry",
    "WorkspaceContext",
    "all_builtin_tools",
    "builtin_registry",
    "discover_agents",
    "discover_skills",
    "__version__",
]
