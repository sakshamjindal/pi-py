"""Agent lifecycle wrapper around the free :mod:`pyharness.agent_loop` kernel.

The kernel is stateless. ``Agent`` owns the lifecycle pieces an embedder
typically wants together: queues for steering and follow-up, an abort
event, a session log writer, listener fan-out via the event bus, cost
accumulation, and resume-from-session.

For embedders who want a different lifecycle (sub-agents that share a
parent's session, web embeddings without local disk, tests that just
collect events into a list), import ``agent_loop`` and ``agent_loop_continue``
directly and supply your own state management.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .agent_loop import LoopConfig, LoopResult, agent_loop, agent_loop_continue
from .compaction import Compactor
from .events import (
    AgentEvent,
    LifecycleEvent,
    SessionEndEvent,
    SessionStartEvent,
    UserMessageEvent,
)
from .extensions import EventBus, HandlerContext, HookOutcome
from .llm import LLMClient
from .queues import AgentHandle, MessageQueue
from .session import Session
from .tools.base import ToolRegistry
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
    # ``parallel`` lets independent tool calls run concurrently. Tools
    # marked ``execution_mode="sequential"`` (e.g. Edit, Write, Bash)
    # force the whole batch to serialise. Default is sequential to match
    # historical behaviour; opt in per agent.
    tool_execution: Literal["parallel", "sequential"] = "sequential"
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
        # Persistent transcript across run/continue calls within one Agent.
        self._messages: list[Message] = []
        self._messages_initialised = False

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
            continue_fn=self.continue_run,
        )

    async def continue_run(self) -> RunResult:
        """Resume the loop from existing context without appending a new
        prompt. Useful after an LLM error or a user-initiated abort —
        the transcript is intact and the next call retries cleanly,
        preserving the cache prefix.

        Raises if called before any prompt has been processed (no
        transcript) or if the last message is from the assistant.
        """

        if not self._messages_initialised:
            raise RuntimeError(
                "continue_run() requires an existing transcript; call run()/start() first"
            )
        return await self._continue()

    # ------------------------------------------------------------------
    # The loop (delegating to the free kernel)
    # ------------------------------------------------------------------

    async def _loop(self, initial_prompt: str) -> RunResult:
        await self._emit_session_start(initial_prompt)
        self._initialise_messages()

        config = self._build_config()
        loop_result = await agent_loop(
            initial_prompt=initial_prompt,
            messages=self._messages,
            config=config,
            tool_registry=self.tool_registry,
            llm=self.llm,
            session_appender=self.session.append_event,
            emit_lifecycle=self._emit_lifecycle,
            drain_steering=self._steering.drain,
            drain_followup=self._followup.drain,
            abort_event=self._abort_event,
            files_written=self._files_written,
            user_message_event_factory=lambda content: UserMessageEvent(
                session_id=self.session.session_id, content=content
            ),
            steering_pending=lambda: not self._steering.empty(),
        )
        return await self._finalise(loop_result)

    async def _continue(self) -> RunResult:
        config = self._build_config()
        loop_result = await agent_loop_continue(
            messages=self._messages,
            config=config,
            tool_registry=self.tool_registry,
            llm=self.llm,
            session_appender=self.session.append_event,
            emit_lifecycle=self._emit_lifecycle,
            drain_steering=self._steering.drain,
            drain_followup=self._followup.drain,
            abort_event=self._abort_event,
            files_written=self._files_written,
            steering_pending=lambda: not self._steering.empty(),
        )
        return await self._finalise(loop_result)

    # ------------------------------------------------------------------
    # Plumbing
    # ------------------------------------------------------------------

    def _initialise_messages(self) -> None:
        if self._messages_initialised:
            return
        self._messages.append(Message(role="system", content=self.system_prompt))
        prior = self.session.read_messages() if self.resume else []
        for m in prior:
            self._messages.append(m)
        for m in self.extra_messages:
            self._messages.append(m)
        self._messages_initialised = True

    def _build_config(self) -> LoopConfig:
        return LoopConfig(
            model=self.options.model,
            max_turns=self.options.max_turns,
            max_tokens=self.options.max_tokens,
            tool_output_max_bytes=self.options.tool_output_max_bytes,
            tool_output_max_lines=self.options.tool_output_max_lines,
            tool_timeouts=dict(self.options.tool_timeouts),
            tool_execution=self.options.tool_execution,
            model_context_window=self.options.model_context_window,
            compaction_threshold_pct=self.options.compaction_threshold_pct,
            compactor=self.compactor,
            session_id=self.session.session_id,
            run_id=self.run_id,
            workspace=self.workspace,
            settings_snapshot=self.options.settings_snapshot,
        )

    async def _finalise(self, loop_result: LoopResult) -> RunResult:
        self._cost_total += loop_result.cost
        await self.session.append_event(
            SessionEndEvent(
                session_id=self.session.session_id,
                reason=loop_result.reason,  # type: ignore[arg-type]
                final_message=loop_result.final_text,
            )
        )
        await self._emit_lifecycle(
            "session_end",
            {"reason": loop_result.reason, "final_message": loop_result.final_text},
        )
        return RunResult(
            session_id=self.session.session_id,
            final_output=loop_result.final_text,
            turn_count=loop_result.turn_count,
            cost=self._cost_total,
            files_written=list(self._files_written),
            completed=loop_result.completed,
            reason=loop_result.reason,
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
        await self._emit_lifecycle(
            "session_start", {"prompt": initial_prompt, "model": self.options.model}
        )

    async def _emit_lifecycle(self, name: str, payload: dict[str, Any]) -> HookOutcome:
        ctx = HandlerContext(
            settings=self.options.settings_snapshot,
            workspace=self.workspace,
            session_id=self.session.session_id,
            run_id=self.run_id,
        )
        return await self.event_bus.emit(LifecycleEvent(name=name, payload=payload), ctx)
