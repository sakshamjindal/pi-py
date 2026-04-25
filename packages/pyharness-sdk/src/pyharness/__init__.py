"""pyharness — minimal Python agent SDK.

Public kernel surface: agent loop + LLM client + tool ABC + sessions +
queues + events + extension runtime. The application-level scaffolding
(settings, AGENTS.md, named agents, skills, built-in tools, CLI) lives
in the ``harness`` package.
"""

from __future__ import annotations

from .compaction import Compactor, CompactionResult
from .events import (
    AgentEvent,
    AssistantMessageEvent,
    CompactionEvent,
    FollowUpMessageEvent,
    LifecycleEvent,
    SessionEndEvent,
    SessionStartEvent,
    SkillLoadedEvent,
    SteeringMessageEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    UserMessageEvent,
    parse_event,
)
from .extensions import (
    EventBus,
    ExtensionAPI,
    HandlerContext,
    HookOutcome,
    HookResult,
)
from .llm import LLMClient, LLMError, count_tokens
from .loop import Agent, AgentOptions
from .queues import AgentHandle, MessageQueue
from .session import Session, SessionInfo
from .tools.base import (
    Tool,
    ToolContext,
    ToolError,
    ToolExecutionResult,
    ToolRegistry,
    execute_tool,
    safe_path,
)
from .types import (
    LLMResponse,
    Message,
    RunResult,
    StreamEvent,
    TokenUsage,
    ToolCall,
)

__version__ = "0.1.0"

__all__ = [
    "Agent",
    "AgentEvent",
    "AgentHandle",
    "AgentOptions",
    "AssistantMessageEvent",
    "Compactor",
    "CompactionEvent",
    "CompactionResult",
    "EventBus",
    "ExtensionAPI",
    "FollowUpMessageEvent",
    "HandlerContext",
    "HookOutcome",
    "HookResult",
    "LLMClient",
    "LLMError",
    "LLMResponse",
    "LifecycleEvent",
    "Message",
    "MessageQueue",
    "RunResult",
    "Session",
    "SessionEndEvent",
    "SessionInfo",
    "SessionStartEvent",
    "SkillLoadedEvent",
    "SteeringMessageEvent",
    "StreamEvent",
    "TokenUsage",
    "Tool",
    "ToolCall",
    "ToolCallEndEvent",
    "ToolCallStartEvent",
    "ToolContext",
    "ToolError",
    "ToolExecutionResult",
    "ToolRegistry",
    "UserMessageEvent",
    "__version__",
    "count_tokens",
    "execute_tool",
    "parse_event",
    "safe_path",
]
