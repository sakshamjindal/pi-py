"""End-to-end integration tests against OpenRouter.

These tests hit a real LLM and cost money. They are skipped automatically
unless ``OPENROUTER_API_KEY`` is set in the environment, so plain
``pytest`` runs (CI, local) leave them out.

Run only the integration suite::

    set -a; source .env; set +a
    pytest -m integration

Override the model::

    PI_INTEGRATION_MODEL=openrouter/anthropic/claude-haiku-4.5 pytest -m integration

What's covered (the three behavioural changes from the agent-loop
convergence work):

1. Parallel tool dispatch — JSONL log shows all ``tool_call_start`` events
   land before any ``tool_call_end`` (i.e. tools genuinely overlap), and
   wall-clock is faster than sequential by at least one tool's worth of
   delay.
2. Terminate signal — a ``done`` tool returning ``ToolResult(terminate=True)``
   exits the run in one turn rather than two.
3. ``continue_run`` — after a forced LLM error the transcript is intact
   and ``continue_run()`` recovers cleanly.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from pyharness import (
    Agent,
    AgentOptions,
    EventBus,
    LLMClient,
    LLMResponse,
    Session,
    Tool,
    ToolContext,
    ToolRegistry,
    ToolResult,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("OPENROUTER_API_KEY"),
        reason="OPENROUTER_API_KEY not set; integration suite skipped",
    ),
]

MODEL = os.environ.get("PI_INTEGRATION_MODEL", "openrouter/anthropic/claude-sonnet-4.5")
TOOL_DELAY_S = 1.5  # makes the parallel/sequential delta measurable


# ---------------------------------------------------------------------------
# Shared fixtures (tools, workspace, agent factory)
# ---------------------------------------------------------------------------


class _ReadArgs(BaseModel):
    path: str = Field(description="Path relative to the workspace.")


class _DelayedReadTool(Tool):
    """Reads a file with an artificial sleep so the parallel speedup is
    larger than network jitter."""

    name = "read_file"
    description = "Read a small text file from the workspace and return its contents."
    args_schema = _ReadArgs

    async def execute(self, args: _ReadArgs, ctx: ToolContext):  # type: ignore[override]
        await asyncio.sleep(TOOL_DELAY_S)
        target = ctx.workspace / args.path
        if not target.is_file():
            return f"missing: {args.path}"
        return target.read_text(encoding="utf-8")


class _DoneArgs(BaseModel):
    reason: str = Field(description="Short reason the task is complete.")


class _DoneTool(Tool):
    name = "done"
    description = (
        "Call this when the task is complete. Returns terminate=True so the "
        "agent stops without one more LLM round-trip."
    )
    args_schema = _DoneArgs

    async def execute(self, args: _DoneArgs, ctx: ToolContext):  # type: ignore[override]
        return ToolResult(content=f"acknowledged: {args.reason}", terminate=True)


def _make_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "alpha.txt").write_text("alpha line\n", encoding="utf-8")
    (ws / "bravo.txt").write_text("bravo line\n", encoding="utf-8")
    (ws / "charlie.txt").write_text("charlie line\n", encoding="utf-8")
    return ws


def _build_agent(
    *,
    tmp_path: Path,
    tool_execution: str,
    tools: list[Tool],
    system_prompt: str,
    llm: LLMClient | None = None,
) -> Agent:
    workspace = _make_workspace(tmp_path)
    registry = ToolRegistry()
    for t in tools:
        registry.register(t)
    session = Session.new(workspace, base_dir=tmp_path / "sessions")
    return Agent(
        AgentOptions(
            model=MODEL,
            max_turns=8,
            tool_execution=tool_execution,  # type: ignore[arg-type]
        ),
        system_prompt=system_prompt,
        tool_registry=registry,
        session=session,
        event_bus=EventBus(),
        workspace=workspace,
        llm=llm or LLMClient(),
    )


def _read_session_events(session: Session) -> list[dict]:
    events: list[dict] = []
    with session.log_path.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


PROMPT_THREE_READS = (
    "Read the contents of alpha.txt, bravo.txt, and charlie.txt using the "
    "read_file tool. Issue all three tool calls in your single response. "
    "Then summarise what you found in one sentence."
)


# ---------------------------------------------------------------------------
# (1) Parallel vs sequential tool dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parallel_dispatch_overlaps_tool_calls(tmp_path):
    """In parallel mode the JSONL log shows every ``tool_call_start``
    landing before any ``tool_call_end``. That's the structural proof of
    concurrency, independent of wall time."""

    agent = _build_agent(
        tmp_path=tmp_path,
        tool_execution="parallel",
        tools=[_DelayedReadTool()],
        system_prompt="You are a helpful agent.",
    )
    result = await agent.run(PROMPT_THREE_READS)
    assert result.completed, f"unexpected reason={result.reason}"

    events = _read_session_events(agent.session)
    types = [e["type"] for e in events]
    # Find the run of tool_call_* events for the first batch.
    first_assistant_with_tools = next(
        (
            i
            for i, e in enumerate(events)
            if e["type"] == "assistant_message" and e.get("tool_calls")
        ),
        None,
    )
    assert first_assistant_with_tools is not None, types

    # Skip past the assistant message; collect the contiguous tool events.
    tool_events = []
    for e in events[first_assistant_with_tools + 1 :]:
        if e["type"] in ("tool_call_start", "tool_call_end"):
            tool_events.append(e["type"])
        else:
            break

    # In parallel mode all 3 starts should fire before any ends. In sequential
    # mode we'd see start/end/start/end/start/end.
    assert tool_events[:3] == ["tool_call_start"] * 3, (
        f"expected 3 starts before any end, got: {tool_events}"
    )


@pytest.mark.asyncio
async def test_parallel_dispatch_is_faster_than_sequential(tmp_path):
    """Wall-clock check: parallel is at least one tool-delay faster than
    sequential. Loose threshold to absorb LLM jitter."""

    parallel_agent = _build_agent(
        tmp_path=tmp_path / "p",
        tool_execution="parallel",
        tools=[_DelayedReadTool()],
        system_prompt="You are a helpful agent.",
    )
    sequential_agent = _build_agent(
        tmp_path=tmp_path / "s",
        tool_execution="sequential",
        tools=[_DelayedReadTool()],
        system_prompt="You are a helpful agent.",
    )

    started = time.perf_counter()
    p_result = await parallel_agent.run(PROMPT_THREE_READS)
    parallel_elapsed = time.perf_counter() - started
    assert p_result.completed

    started = time.perf_counter()
    s_result = await sequential_agent.run(PROMPT_THREE_READS)
    sequential_elapsed = time.perf_counter() - started
    assert s_result.completed

    # Sequential runs 3 reads back-to-back (~3 * TOOL_DELAY_S in tool phase)
    # vs parallel's ~1 * TOOL_DELAY_S. Require at least one whole tool-delay
    # of headroom; the rest is LLM time which is roughly equal across runs.
    assert sequential_elapsed > parallel_elapsed + TOOL_DELAY_S, (
        f"parallel={parallel_elapsed:.2f}s sequential={sequential_elapsed:.2f}s "
        f"(expected sequential to be at least {TOOL_DELAY_S}s slower)"
    )


# ---------------------------------------------------------------------------
# (2) Terminate signal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_terminate_signal_exits_in_one_turn(tmp_path):
    """A tool returning ``terminate=True`` ends the run after the tool
    batch without burning another LLM round-trip."""

    agent = _build_agent(
        tmp_path=tmp_path,
        tool_execution="sequential",
        tools=[_DoneTool()],
        system_prompt=(
            "You are a task-completion agent. When the user gives you any "
            "task, immediately call the `done` tool with a one-sentence reason. "
            "Do NOT produce additional commentary after calling `done`."
        ),
    )
    result = await agent.run("Confirm receipt and stop. Use the done tool.")
    assert result.completed
    assert result.reason == "completed"
    assert result.turn_count == 1, (
        f"expected 1 turn (terminate=True short-circuit); got {result.turn_count}"
    )


# ---------------------------------------------------------------------------
# (3) continue_run after error
# ---------------------------------------------------------------------------


class _OneShotFailingLLM:
    """Wraps a real LLMClient; raises on the first ``complete`` call."""

    def __init__(self, real: LLMClient):
        self._real = real
        self._failed_once = False

    async def complete(self, **kwargs) -> LLMResponse:
        if not self._failed_once:
            self._failed_once = True
            raise RuntimeError("simulated transient network error")
        return await self._real.complete(**kwargs)

    async def stream(self, **kwargs):
        async for ev in self._real.stream(**kwargs):
            yield ev


@pytest.mark.asyncio
async def test_continue_run_recovers_from_error(tmp_path):
    """First run errors out; ``continue_run`` resumes with the same
    transcript and produces a real assistant response."""

    agent = _build_agent(
        tmp_path=tmp_path,
        tool_execution="sequential",
        tools=[],
        system_prompt="You are a helpful agent. Reply concisely.",
        llm=_OneShotFailingLLM(LLMClient()),  # type: ignore[arg-type]
    )

    first = await agent.run("Say a short hello in five words or fewer.")
    assert first.reason == "error", f"unexpected first reason: {first.reason}"

    second = await agent.continue_run()
    assert second.reason == "completed", f"unexpected second reason: {second.reason}"
    assert second.final_output, "expected a non-empty assistant response"
