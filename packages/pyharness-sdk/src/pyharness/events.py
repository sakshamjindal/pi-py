"""Typed event payloads for the session log and the lifecycle event bus.

The session log persists ``SessionEvent`` subclasses as JSON Lines.
Lifecycle events (used by extensions) are passed through the event bus
and may or may not be persisted by handlers themselves.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class AgentEvent(BaseModel):
    """Base class. All session-log events derive from this."""

    model_config = ConfigDict(extra="ignore")

    type: str
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    session_id: str
    timestamp: float = Field(default_factory=time.time)
    sequence_number: int = 0


# ---------------------------------------------------------------------------
# Session-log events
# ---------------------------------------------------------------------------


class SessionStartEvent(AgentEvent):
    type: Literal["session_start"] = "session_start"
    cwd: str
    model: str
    agent_name: str | None = None
    system_prompt_hash: str
    settings_snapshot: dict[str, Any] = Field(default_factory=dict)


class UserMessageEvent(AgentEvent):
    type: Literal["user_message"] = "user_message"
    content: str


class AssistantMessageEvent(AgentEvent):
    type: Literal["assistant_message"] = "assistant_message"
    text: str = ""
    thinking: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)


class ToolCallStartEvent(AgentEvent):
    type: Literal["tool_call_start"] = "tool_call_start"
    call_id: str
    tool_name: str
    arguments: dict[str, Any]


class ToolCallEndEvent(AgentEvent):
    type: Literal["tool_call_end"] = "tool_call_end"
    call_id: str
    tool_name: str
    ok: bool
    result: str
    error: str | None = None
    duration_ms: float = 0.0


class CompactionEvent(AgentEvent):
    type: Literal["compaction"] = "compaction"
    tokens_before: int
    tokens_after: int
    summary: str


class SteeringMessageEvent(AgentEvent):
    type: Literal["steering_message"] = "steering_message"
    content: str


class FollowUpMessageEvent(AgentEvent):
    type: Literal["followup_message"] = "followup_message"
    content: str


class SkillLoadedEvent(AgentEvent):
    type: Literal["skill_loaded"] = "skill_loaded"
    name: str
    tools_added: list[str]


class SessionEndEvent(AgentEvent):
    type: Literal["session_end"] = "session_end"
    reason: Literal["completed", "aborted", "error", "max_turns"]
    final_message: str = ""


# ---------------------------------------------------------------------------
# Lifecycle events (event bus only; not necessarily persisted)
# ---------------------------------------------------------------------------


class LifecycleEvent(BaseModel):
    """Events that flow through the event bus to extensions/handlers."""

    model_config = ConfigDict(extra="allow")

    name: str
    payload: dict[str, Any] = Field(default_factory=dict)


# Mapping used by Session.read_messages and the JSONL parser.
EVENT_TYPES: dict[str, type[AgentEvent]] = {
    "session_start": SessionStartEvent,
    "user_message": UserMessageEvent,
    "assistant_message": AssistantMessageEvent,
    "tool_call_start": ToolCallStartEvent,
    "tool_call_end": ToolCallEndEvent,
    "compaction": CompactionEvent,
    "steering_message": SteeringMessageEvent,
    "followup_message": FollowUpMessageEvent,
    "skill_loaded": SkillLoadedEvent,
    "session_end": SessionEndEvent,
}


def parse_event(raw: dict[str, Any]) -> AgentEvent:
    cls = EVENT_TYPES.get(raw.get("type", ""))
    if cls is None:
        return AgentEvent.model_validate(raw)
    return cls.model_validate(raw)
