"""Shared Pydantic models used across pyharness subsystems."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ToolCall(BaseModel):
    """A single tool invocation requested by the LLM."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0


class LLMResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str = ""
    thinking: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    raw: dict[str, Any] = Field(default_factory=dict)
    finish_reason: str | None = None


class StreamEvent(BaseModel):
    """A single event yielded from the streaming completion path."""

    model_config = ConfigDict(extra="ignore")

    type: Literal[
        "text_delta",
        "tool_call_start",
        "tool_call_delta",
        "tool_call_end",
        "message_stop",
        "usage",
    ]
    delta: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    arguments_delta: str | None = None
    arguments: dict[str, Any] | None = None
    usage: TokenUsage | None = None
    finish_reason: str | None = None


class Message(BaseModel):
    """A single chat message in OpenAI/LiteLLM format."""

    model_config = ConfigDict(extra="allow")

    role: Literal["system", "user", "assistant", "tool"]
    content: Any = None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class RunResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    session_id: str
    final_output: str
    turn_count: int
    cost: float
    files_written: list[str] = Field(default_factory=list)
    completed: bool
    reason: str
