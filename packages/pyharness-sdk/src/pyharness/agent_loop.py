"""Pure agent-loop kernel.

Two free coroutines:

* :func:`agent_loop` — start a new turn cycle, optionally appending an
  initial user prompt.
* :func:`agent_loop_continue` — resume from existing context without
  appending a new prompt. Used for clean post-error retries.

Both take all dependencies as arguments (no state on ``self``). The
:class:`pyharness.Agent` wrapper builds the dependencies from a session,
queues, abort event, and event bus, and forwards calls here.

This module does not own queues, listeners, or persistence. It calls
hooks the wrapper provides. That's what lets the same kernel power a
CLI, a sub-agent, a web embedding, or a test — each builds its own
lifecycle around the same loop.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .compaction import Compactor
from .events import (
    AgentEvent,
    AssistantMessageEvent,
    CompactionEvent,
    FollowUpMessageEvent,
    SteeringMessageEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)
from .extensions import HookOutcome, HookResult
from .llm import LLMClient
from .tools.base import Tool, ToolContext, ToolExecutionResult, ToolRegistry, execute_tool
from .types import Message, ToolCall

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

ToolExecution = Literal["parallel", "sequential"]

EventSink = Callable[[AgentEvent], Awaitable[Any]]
LifecycleEmitter = Callable[[str, dict[str, Any]], Awaitable[HookOutcome]]
QueueDrainer = Callable[[], Awaitable[list[str]]]


@dataclass
class LoopConfig:
    """Per-run configuration. Built by ``Agent`` from ``AgentOptions``
    plus the dependencies the kernel needs to do its job.
    """

    # LLM / tool dispatch
    model: str
    max_turns: int
    max_tokens: int | None
    tool_output_max_bytes: int
    tool_output_max_lines: int
    tool_timeouts: dict[str, float]
    tool_execution: ToolExecution
    # Compaction
    model_context_window: int
    compaction_threshold_pct: float
    compactor: Compactor | None
    # Identity / observability
    session_id: str
    run_id: str
    workspace: Path
    settings_snapshot: dict[str, Any]


@dataclass
class LoopResult:
    final_text: str
    turn_count: int
    completed: bool
    reason: str
    cost: float


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


async def agent_loop(
    *,
    initial_prompt: str,
    messages: list[Message],
    config: LoopConfig,
    tool_registry: ToolRegistry,
    llm: LLMClient,
    session_appender: EventSink,
    emit_lifecycle: LifecycleEmitter,
    drain_steering: QueueDrainer,
    drain_followup: QueueDrainer,
    abort_event: asyncio.Event,
    files_written: list[str],
    user_message_event_factory: Callable[[str], AgentEvent],
    steering_pending: Callable[[], bool] | None = None,
) -> LoopResult:
    """Run the loop with an optional initial user prompt appended.

    ``messages`` is mutated in place — the caller passes the assembled
    transcript (system prompt + prior + extra) and gets it back populated
    with the new turns.
    """

    if initial_prompt:
        await session_appender(user_message_event_factory(initial_prompt))
        messages.append(Message(role="user", content=initial_prompt))

    return await _run_loop(
        messages=messages,
        config=config,
        tool_registry=tool_registry,
        llm=llm,
        session_appender=session_appender,
        emit_lifecycle=emit_lifecycle,
        drain_steering=drain_steering,
        drain_followup=drain_followup,
        abort_event=abort_event,
        files_written=files_written,
        steering_pending=steering_pending,
    )


async def agent_loop_continue(
    *,
    messages: list[Message],
    config: LoopConfig,
    tool_registry: ToolRegistry,
    llm: LLMClient,
    session_appender: EventSink,
    emit_lifecycle: LifecycleEmitter,
    drain_steering: QueueDrainer,
    drain_followup: QueueDrainer,
    abort_event: asyncio.Event,
    files_written: list[str],
    steering_pending: Callable[[], bool] | None = None,
) -> LoopResult:
    """Resume from existing context without appending a prompt.

    Precondition: the last non-system message must be ``user`` or
    ``tool``. Continuing from an ``assistant`` message would either send
    a malformed request (an assistant message followed by another
    assistant turn) or duplicate the previous response. The caller is
    responsible for cleaning up partial tool batches before calling this
    (synthesise error tool results for in-flight calls if needed).
    """

    if not messages:
        raise ValueError("agent_loop_continue: messages is empty")

    # Find the last non-system message; system can be in any position
    # but conventionally is at index 0.
    last_non_system = next(
        (m for m in reversed(messages) if m.role != "system"), None
    )
    if last_non_system is None:
        raise ValueError("agent_loop_continue: no non-system messages to continue from")
    if last_non_system.role == "assistant":
        raise ValueError(
            "agent_loop_continue: cannot continue from an assistant message — "
            "the last message must be 'user' or 'tool'"
        )

    return await _run_loop(
        messages=messages,
        config=config,
        tool_registry=tool_registry,
        llm=llm,
        session_appender=session_appender,
        emit_lifecycle=emit_lifecycle,
        drain_steering=drain_steering,
        drain_followup=drain_followup,
        abort_event=abort_event,
        files_written=files_written,
        steering_pending=steering_pending,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _run_loop(
    *,
    messages: list[Message],
    config: LoopConfig,
    tool_registry: ToolRegistry,
    llm: LLMClient,
    session_appender: EventSink,
    emit_lifecycle: LifecycleEmitter,
    drain_steering: QueueDrainer,
    drain_followup: QueueDrainer,
    abort_event: asyncio.Event,
    files_written: list[str],
    steering_pending: Callable[[], bool] | None = None,
) -> LoopResult:
    turn = 0
    final_text = ""
    completed = False
    reason = "max_turns"
    cost_total = 0.0

    while turn < config.max_turns:
        if abort_event.is_set():
            reason = "aborted"
            break
        turn += 1

        await _drain_into_messages(
            messages,
            session_appender=session_appender,
            emit_lifecycle=emit_lifecycle,
            session_id=config.session_id,
            steering_drainer=drain_steering,
            followup_drainer=drain_followup,
        )
        messages = await _maybe_compact(
            messages,
            config=config,
            session_appender=session_appender,
            emit_lifecycle=emit_lifecycle,
        )

        await emit_lifecycle("turn_start", {"turn": turn})

        outcome = await emit_lifecycle(
            "before_llm_call", {"messages": [m.model_dump() for m in messages]}
        )
        if outcome.result is HookResult.Deny:
            reason = "error"
            final_text = f"LLM call denied by extension: {outcome.reason}"
            break

        try:
            response = await llm.complete(
                model=config.model,
                messages=messages,
                tools=tool_registry.list_specs() or None,
                max_tokens=config.max_tokens,
            )
        except Exception as exc:
            reason = "error"
            final_text = f"LLM error: {exc}"
            break

        cost_total += response.usage.cost_usd
        await emit_lifecycle("after_llm_call", {"response": response.model_dump()})

        tc_dicts = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
            }
            for tc in response.tool_calls
        ]

        await session_appender(
            AssistantMessageEvent(
                session_id=config.session_id,
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

        # Three-phase tool dispatch: preflight (sequential), execute
        # (parallel or sequential), persist (assistant-source order).
        batch = await _dispatch_tool_batch(
            response.tool_calls,
            tool_registry=tool_registry,
            config=config,
            session_appender=session_appender,
            emit_lifecycle=emit_lifecycle,
            abort_event=abort_event,
            files_written=files_written,
            steering_pending=steering_pending or (lambda: False),
            current_messages=messages,
        )

        # Append tool messages in assistant source order. The dispatcher
        # already preserved that order in batch.results.
        for tool_msg in batch.tool_messages:
            messages.append(tool_msg)

        if abort_event.is_set():
            reason = "aborted"
            break

        # `terminate: True` from every tool in the batch ⇒ skip the
        # next LLM call. Mixed batches continue normally.
        if batch.results and all(r.terminate for r in batch.results):
            final_text = response.text or "(terminated by tool)"
            completed = True
            reason = "completed"
            break

        # If steering arrived during the batch, drain it and re-loop
        # without ending the turn. (In parallel mode we can only check
        # at batch boundary; in sequential mode the dispatcher checks
        # between calls.)
        if batch.steered_mid_turn:
            await _drain_into_messages(
                messages,
                session_appender=session_appender,
                emit_lifecycle=emit_lifecycle,
                session_id=config.session_id,
                steering_drainer=drain_steering,
                followup_drainer=None,
            )
            continue

        await emit_lifecycle("turn_end", {"turn": turn})

    if not completed and reason == "max_turns":
        final_text = "Reached max_turns without completing."

    return LoopResult(
        final_text=final_text,
        turn_count=turn,
        completed=completed,
        reason=reason,
        cost=cost_total,
    )


# ---------------------------------------------------------------------------
# Tool batch dispatch
# ---------------------------------------------------------------------------


@dataclass
class _PreparedCall:
    tool_call: ToolCall
    tool: Tool


@dataclass
class _ToolBatch:
    tool_messages: list[Message]
    results: list[ToolExecutionResult]
    steered_mid_turn: bool


async def _dispatch_tool_batch(
    tool_calls: Sequence[ToolCall],
    *,
    tool_registry: ToolRegistry,
    config: LoopConfig,
    session_appender: EventSink,
    emit_lifecycle: LifecycleEmitter,
    abort_event: asyncio.Event,
    files_written: list[str],
    steering_pending: Callable[[], bool],
    current_messages: list[Message],
) -> _ToolBatch:
    # ---- Preflight (always sequential) ----
    # Either resolves to a runnable (_PreparedCall) or an "immediate"
    # synthetic result keyed by call id.
    runnable_by_id: dict[str, _PreparedCall] = {}
    immediate: dict[str, tuple[Message, ToolExecutionResult]] = {}

    for tc in tool_calls:
        tool = tool_registry.get(tc.name)
        if tool is None:
            err = f"Unknown tool: {tc.name}"
            ter = ToolExecutionResult(ok=False, content=err, error="unknown_tool")
            await _record_tool_end(
                tc.id, tc.name, ter, session_appender, config.session_id
            )
            msg = Message(role="tool", tool_call_id=tc.id, name=tc.name, content=err)
            immediate[tc.id] = (msg, ter)
            continue

        hook_outcome = await emit_lifecycle(
            "before_tool_call",
            {"tool_name": tc.name, "arguments": tc.arguments, "call_id": tc.id},
        )
        if hook_outcome.result is HookResult.Deny:
            text = f"Denied by extension: {hook_outcome.reason or 'no reason given'}"
            ter = ToolExecutionResult(ok=False, content=text, error="denied")
            await _record_tool_end(tc.id, tc.name, ter, session_appender, config.session_id)
            msg = Message(role="tool", tool_call_id=tc.id, name=tc.name, content=text)
            immediate[tc.id] = (msg, ter)
            continue
        if hook_outcome.result is HookResult.Replace:
            replacement = hook_outcome.replacement_value
            text = (
                replacement
                if isinstance(replacement, str)
                else json.dumps(replacement, default=str)
            )
            ter = ToolExecutionResult(ok=True, content=text)
            await _record_tool_end(tc.id, tc.name, ter, session_appender, config.session_id)
            msg = Message(role="tool", tool_call_id=tc.id, name=tc.name, content=text)
            immediate[tc.id] = (msg, ter)
            continue

        runnable_by_id[tc.id] = _PreparedCall(tool_call=tc, tool=tool)

    # ---- Decide execution mode ----
    runnable = list(runnable_by_id.values())
    has_sequential_tool = any(
        getattr(p.tool, "execution_mode", "parallel") == "sequential" for p in runnable
    )
    use_parallel = (
        config.tool_execution == "parallel" and not has_sequential_tool and len(runnable) > 1
    )

    # ---- Execute ----
    executed: dict[str, ToolExecutionResult] = {}
    steered_mid_turn = False

    async def _run_one(prep: _PreparedCall) -> tuple[str, ToolExecutionResult]:
        tc = prep.tool_call
        await session_appender(
            ToolCallStartEvent(
                session_id=config.session_id,
                call_id=tc.id,
                tool_name=tc.name,
                arguments=tc.arguments,
            )
        )
        ctx = ToolContext(
            workspace=config.workspace,
            session_id=config.session_id,
            run_id=config.run_id,
            settings=config.settings_snapshot,
            extras={"files_written": files_written},
        )
        ter = await execute_tool(
            prep.tool,
            tc.arguments,
            ctx,
            timeout_seconds=config.tool_timeouts.get(prep.tool.name),
            max_bytes=config.tool_output_max_bytes,
            max_lines=config.tool_output_max_lines,
        )
        await _record_tool_end(tc.id, tc.name, ter, session_appender, config.session_id)
        await emit_lifecycle(
            "after_tool_call",
            {
                "tool_name": tc.name,
                "ok": ter.ok,
                "result": ter.content,
                "duration_ms": ter.duration_ms,
                "terminate": ter.terminate,
            },
        )
        return tc.id, ter

    if use_parallel:
        results = await asyncio.gather(
            *[_run_one(p) for p in runnable], return_exceptions=False
        )
        for call_id, ter in results:
            executed[call_id] = ter
    else:
        for prep in runnable:
            if abort_event.is_set():
                break
            call_id, ter = await _run_one(prep)
            executed[call_id] = ter
            # Mid-batch steering check: only meaningful in sequential mode.
            # The wrapper exposes the queue via the drain_steering closure;
            # we look at it indirectly through ``steering_pending``.
            if steering_pending():
                steered_mid_turn = True
                break

    # ---- Persist in assistant source order ----
    tool_messages: list[Message] = []
    results: list[ToolExecutionResult] = []
    for tc in tool_calls:
        if tc.id in immediate:
            msg, ter = immediate[tc.id]
            tool_messages.append(msg)
            results.append(ter)
            continue
        if tc.id not in executed:
            # Bailed out (abort or steering) before running this call. Synthesise
            # an error tool message so the transcript is well-formed.
            err = "Tool call skipped (aborted or steered)"
            ter = ToolExecutionResult(ok=False, content=err, error="skipped")
            await _record_tool_end(tc.id, tc.name, ter, session_appender, config.session_id)
            tool_messages.append(
                Message(role="tool", tool_call_id=tc.id, name=tc.name, content=err)
            )
            results.append(ter)
            continue
        ter = executed[tc.id]
        tool_messages.append(
            Message(
                role="tool",
                tool_call_id=tc.id,
                name=tc.name,
                content=ter.content,
            )
        )
        results.append(ter)

    return _ToolBatch(
        tool_messages=tool_messages,
        results=results,
        steered_mid_turn=steered_mid_turn,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _drain_into_messages(
    messages: list[Message],
    *,
    session_appender: EventSink,
    emit_lifecycle: LifecycleEmitter,
    session_id: str,
    steering_drainer: QueueDrainer | None,
    followup_drainer: QueueDrainer | None,
) -> None:
    if steering_drainer is not None:
        for content in await steering_drainer():
            await session_appender(
                SteeringMessageEvent(session_id=session_id, content=content)
            )
            await emit_lifecycle("steering_received", {"content": content})
            messages.append(Message(role="user", content=f"[steering] {content}"))
    if followup_drainer is not None:
        for content in await followup_drainer():
            await session_appender(
                FollowUpMessageEvent(session_id=session_id, content=content)
            )
            await emit_lifecycle("followup_received", {"content": content})
            messages.append(Message(role="user", content=content))


async def _maybe_compact(
    messages: list[Message],
    *,
    config: LoopConfig,
    session_appender: EventSink,
    emit_lifecycle: LifecycleEmitter,
) -> list[Message]:
    if config.compactor is None:
        return messages
    threshold = int(config.model_context_window * config.compaction_threshold_pct)
    await emit_lifecycle("compaction_start", {})
    try:
        result = await config.compactor.maybe_compact(
            messages, threshold, model_for_count=config.model
        )
    except Exception as exc:
        sys.stderr.write(f"[compaction] failed: {exc}; continuing without compaction\n")
        await emit_lifecycle("compaction_end", {"compacted": False, "error": str(exc)})
        return messages
    if result.compacted:
        await session_appender(
            CompactionEvent(
                session_id=config.session_id,
                tokens_before=result.tokens_before,
                tokens_after=result.tokens_after,
                summary=result.summary,
            )
        )
    await emit_lifecycle(
        "compaction_end",
        {
            "compacted": result.compacted,
            "tokens_before": result.tokens_before,
            "tokens_after": result.tokens_after,
        },
    )
    return result.messages


async def _record_tool_end(
    call_id: str,
    name: str,
    ter: ToolExecutionResult,
    session_appender: EventSink,
    session_id: str,
) -> None:
    await session_appender(
        ToolCallEndEvent(
            session_id=session_id,
            call_id=call_id,
            tool_name=name,
            ok=ter.ok,
            result=ter.content,
            error=ter.error,
            duration_ms=ter.duration_ms,
        )
    )


