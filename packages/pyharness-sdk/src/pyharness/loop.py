"""Core agent loop.

The loop is straight-line code:

  for turn in range(max_turns):
      drain steering / follow-up queues
      maybe compact
      call LLM
      if no tool_calls: done
      execute each tool_call (checking steering between calls)

Lifecycle events are emitted to the event bus so extensions can observe
or override; the session log records the durable record of what happened.

This module is the SDK kernel: it knows nothing about settings.json,
AGENTS.md, named agents, skills, or built-in tools. The harness package
assembles those pieces into a system prompt + tool registry + options
and hands them to ``Agent``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .compaction import Compactor
from .events import (
    AssistantMessageEvent,
    CompactionEvent,
    FollowUpMessageEvent,
    LifecycleEvent,
    SessionEndEvent,
    SessionStartEvent,
    SteeringMessageEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    UserMessageEvent,
)
from .extensions import EventBus, HandlerContext, HookOutcome, HookResult
from .llm import LLMClient
from .queues import AgentHandle, MessageQueue
from .session import Session
from .tools.base import Tool, ToolContext, ToolRegistry, execute_tool
from .types import Message, RunResult


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class AgentOptions:
    """Loop-only configuration. The assembly layer (e.g. ``harness``)
    builds this from its richer Settings object."""

    model: str
    max_turns: int = 100
    model_context_window: int = 200_000
    compaction_threshold_pct: float = 0.8
    tool_output_max_bytes: int = 51_200
    tool_output_max_lines: int = 2000
    tool_timeouts: dict[str, float] = field(default_factory=dict)
    max_tokens: int | None = None
    # Recorded in the session_start event for traceability. The SDK does
    # not interpret these.
    agent_name: str | None = None
    settings_snapshot: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class Agent:
    """The pure agent loop. Construct with already-built components."""

    def __init__(
        self,
        options: AgentOptions,
        *,
        system_prompt: str,
        tool_registry: ToolRegistry,
        session: Session,
        event_bus: EventBus,
        workspace: Path,
        llm: LLMClient | None = None,
        compactor: Compactor | None = None,
        run_id: str | None = None,
        extra_messages: list[Message] | None = None,
        resume: bool = False,
    ):
        self.options = options
        self.system_prompt = system_prompt
        self.tool_registry = tool_registry
        self.session = session
        self.event_bus = event_bus
        self.workspace = workspace
        self.llm = llm or LLMClient()
        self.compactor = compactor
        self.run_id = run_id or uuid.uuid4().hex
        self.extra_messages = list(extra_messages or [])
        self.resume = resume
        self._steering = MessageQueue()
        self._followup = MessageQueue()
        self._abort_event = asyncio.Event()
        self._cost_total = 0.0
        self._files_written: list[str] = []

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    async def run(self, prompt: str) -> RunResult:
        return await self._loop(prompt)

    def start(self, prompt: str) -> AgentHandle:
        task = asyncio.create_task(self._loop(prompt))
        return AgentHandle(
            steering=self._steering,
            follow_up=self._followup,
            abort_event=self._abort_event,
            task=task,
        )

    # ------------------------------------------------------------------
    # The loop
    # ------------------------------------------------------------------

    async def _loop(self, initial_prompt: str) -> RunResult:
        await self._emit_session_start(initial_prompt)

        # Reconstruct messages on resume; otherwise start fresh.
        messages: list[Message] = []
        messages.append(Message(role="system", content=self.system_prompt))
        prior = self.session.read_messages() if self.resume else []
        for m in prior:
            messages.append(m)
        for m in self.extra_messages:
            messages.append(m)

        if initial_prompt:
            await self.session.append_event(
                UserMessageEvent(session_id=self.session.session_id, content=initial_prompt)
            )
            messages.append(Message(role="user", content=initial_prompt))

        turn = 0
        final_text = ""
        completed = False
        reason = "max_turns"

        while turn < self.options.max_turns:
            if self._abort_event.is_set():
                reason = "aborted"
                break
            turn += 1

            messages = await self._drain_queues_into(messages, kind="both")

            messages = await self._maybe_compact(messages)

            await self._emit_lifecycle("turn_start", {"turn": turn})

            outcome = await self._emit_lifecycle(
                "before_llm_call", {"messages": [m.model_dump() for m in messages]}
            )
            if outcome.result is HookResult.Deny:
                reason = "error"
                final_text = f"LLM call denied by extension: {outcome.reason}"
                break

            try:
                response = await self.llm.complete(
                    model=self.options.model,
                    messages=messages,
                    tools=self.tool_registry.list_specs() or None,
                    max_tokens=self.options.max_tokens,
                )
            except Exception as exc:
                reason = "error"
                final_text = f"LLM error: {exc}"
                break

            self._cost_total += response.usage.cost_usd

            await self._emit_lifecycle("after_llm_call", {"response": response.model_dump()})

            tc_dicts = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                }
                for tc in response.tool_calls
            ]

            await self.session.append_event(
                AssistantMessageEvent(
                    session_id=self.session.session_id,
                    text=response.text,
                    thinking=response.thinking,
                    tool_calls=tc_dicts,
                )
            )
            messages.append(
                Message(
                    role="assistant",
                    content=response.text,
                    tool_calls=tc_dicts or None,
                )
            )

            if not response.tool_calls:
                final_text = response.text
                completed = True
                reason = "completed"
                break

            steered_mid_turn = False
            for tc in response.tool_calls:
                if self._abort_event.is_set():
                    reason = "aborted"
                    break

                tool: Tool | None = self.tool_registry.get(tc.name)
                if tool is None:
                    err = f"Unknown tool: {tc.name}"
                    await self._record_tool_end(tc.id, tc.name, ok=False, content=err, error="unknown_tool")
                    messages.append(
                        Message(role="tool", tool_call_id=tc.id, name=tc.name, content=err)
                    )
                    continue

                hook_outcome = await self._emit_lifecycle(
                    "before_tool_call",
                    {"tool_name": tc.name, "arguments": tc.arguments, "call_id": tc.id},
                )
                if hook_outcome.result is HookResult.Deny:
                    msg = f"Denied by extension: {hook_outcome.reason or 'no reason given'}"
                    await self._record_tool_end(tc.id, tc.name, ok=False, content=msg, error="denied")
                    messages.append(
                        Message(role="tool", tool_call_id=tc.id, name=tc.name, content=msg)
                    )
                    continue
                if hook_outcome.result is HookResult.Replace:
                    replacement = hook_outcome.replacement_value
                    text = replacement if isinstance(replacement, str) else json.dumps(replacement, default=str)
                    await self._record_tool_end(tc.id, tc.name, ok=True, content=text)
                    messages.append(
                        Message(role="tool", tool_call_id=tc.id, name=tc.name, content=text)
                    )
                    continue

                await self.session.append_event(
                    ToolCallStartEvent(
                        session_id=self.session.session_id,
                        call_id=tc.id,
                        tool_name=tc.name,
                        arguments=tc.arguments,
                    )
                )

                ctx = ToolContext(
                    workspace=self.workspace,
                    session_id=self.session.session_id,
                    run_id=self.run_id,
                    event_bus=self.event_bus,
                    settings=self.options.settings_snapshot,
                    extras={"files_written": self._files_written},
                )

                result = await execute_tool(
                    tool,
                    tc.arguments,
                    ctx,
                    timeout_seconds=self.options.tool_timeouts.get(tool.name),
                    max_bytes=self.options.tool_output_max_bytes,
                    max_lines=self.options.tool_output_max_lines,
                )

                await self.session.append_event(
                    ToolCallEndEvent(
                        session_id=self.session.session_id,
                        call_id=tc.id,
                        tool_name=tc.name,
                        ok=result.ok,
                        result=result.content,
                        error=result.error,
                        duration_ms=result.duration_ms,
                    )
                )
                messages.append(
                    Message(
                        role="tool",
                        tool_call_id=tc.id,
                        name=tc.name,
                        content=result.content,
                    )
                )

                await self._emit_lifecycle(
                    "after_tool_call",
                    {
                        "tool_name": tc.name,
                        "ok": result.ok,
                        "result": result.content,
                        "duration_ms": result.duration_ms,
                    },
                )

                # Steering check between tool calls.
                if not self._steering.empty():
                    steered_mid_turn = True
                    break

            if steered_mid_turn:
                messages = await self._drain_queues_into(messages, kind="steering")
                continue

            await self._emit_lifecycle("turn_end", {"turn": turn})

        if not completed and reason == "max_turns":
            final_text = "Reached max_turns without completing."

        await self.session.append_event(
            SessionEndEvent(
                session_id=self.session.session_id,
                reason=reason,  # type: ignore[arg-type]
                final_message=final_text,
            )
        )
        await self._emit_lifecycle("session_end", {"reason": reason, "final_message": final_text})

        return RunResult(
            session_id=self.session.session_id,
            final_output=final_text,
            turn_count=turn,
            cost=self._cost_total,
            files_written=list(self._files_written),
            completed=completed,
            reason=reason,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _drain_queues_into(self, messages: list[Message], *, kind: str) -> list[Message]:
        if kind in ("steering", "both"):
            for content in await self._steering.drain():
                await self.session.append_event(
                    SteeringMessageEvent(session_id=self.session.session_id, content=content)
                )
                await self._emit_lifecycle("steering_received", {"content": content})
                messages.append(Message(role="user", content=f"[steering] {content}"))
        if kind in ("followup", "both"):
            for content in await self._followup.drain():
                await self.session.append_event(
                    FollowUpMessageEvent(session_id=self.session.session_id, content=content)
                )
                await self._emit_lifecycle("followup_received", {"content": content})
                messages.append(Message(role="user", content=content))
        return messages

    async def _maybe_compact(self, messages: list[Message]) -> list[Message]:
        if self.compactor is None:
            return messages
        threshold = int(self.options.model_context_window * self.options.compaction_threshold_pct)
        await self._emit_lifecycle("compaction_start", {})
        result = await self.compactor.maybe_compact(
            messages, threshold, model_for_count=self.options.model
        )
        if result.compacted:
            await self.session.append_event(
                CompactionEvent(
                    session_id=self.session.session_id,
                    tokens_before=result.tokens_before,
                    tokens_after=result.tokens_after,
                    summary=result.summary,
                )
            )
        await self._emit_lifecycle(
            "compaction_end",
            {
                "compacted": result.compacted,
                "tokens_before": result.tokens_before,
                "tokens_after": result.tokens_after,
            },
        )
        return result.messages

    async def _record_tool_end(self, call_id: str, name: str, *, ok: bool, content: str, error: str | None = None) -> None:
        await self.session.append_event(
            ToolCallEndEvent(
                session_id=self.session.session_id,
                call_id=call_id,
                tool_name=name,
                ok=ok,
                result=content,
                error=error,
            )
        )

    async def _emit_session_start(self, initial_prompt: str) -> None:
        digest = hashlib.sha1(self.system_prompt.encode("utf-8")).hexdigest()
        await self.session.append_event(
            SessionStartEvent(
                session_id=self.session.session_id,
                cwd=str(self.workspace),
                model=self.options.model,
                agent_name=self.options.agent_name,
                system_prompt_hash=digest,
                settings_snapshot=self.options.settings_snapshot,
            )
        )
        await self._emit_lifecycle("session_start", {"prompt": initial_prompt, "model": self.options.model})

    async def _emit_lifecycle(self, name: str, payload: dict[str, Any]) -> HookOutcome:
        ctx = HandlerContext(
            settings=self.options.settings_snapshot,
            workspace=self.workspace,
            session_id=self.session.session_id,
            run_id=self.run_id,
        )
        return await self.event_bus.emit(LifecycleEvent(name=name, payload=payload), ctx)
