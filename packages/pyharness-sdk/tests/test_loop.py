"""End-to-end tests for the agent loop with a fake LLM client."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel, Field

from pyharness import (
    Agent,
    AgentOptions,
    EventBus,
    LLMResponse,
    Session,
    Tool,
    ToolCall,
    ToolContext,
    ToolRegistry,
    ToolResult,
)


class _ScriptedLLM:
    """Returns prepared responses in order, ignoring inputs."""

    def __init__(self, responses):
        self._responses = list(responses)

    async def complete(self, **_):
        return self._responses.pop(0)

    async def stream(self, **_):
        if False:
            yield None


class _ReadArgs(BaseModel):
    path: str = Field(description="Path to read.")


class _ReadTool(Tool):
    name = "read"
    description = "Read a file from the workspace."
    args_schema = _ReadArgs

    async def execute(self, args: _ReadArgs, ctx: ToolContext):  # type: ignore[override]
        target = ctx.workspace / args.path
        if not target.is_file():
            return f"missing: {args.path}"
        return target.read_text(encoding="utf-8")


def _make_agent(tmp_path, *, llm, options=None) -> Agent:
    options = options or AgentOptions(model="fake", max_turns=10)
    registry = ToolRegistry()
    registry.register(_ReadTool())
    session = Session.new(tmp_path, base_dir=tmp_path / "sessions")
    return Agent(
        options,
        system_prompt="You are a test agent.",
        tool_registry=registry,
        session=session,
        event_bus=EventBus(),
        workspace=tmp_path,
        llm=llm,
    )


@pytest.mark.asyncio
async def test_loop_terminates_on_no_tool_calls(tmp_path, isolated_session_dir):
    agent = _make_agent(tmp_path, llm=_ScriptedLLM([LLMResponse(text="all done")]))
    result = await agent.run("hello")
    assert result.completed
    assert result.final_output == "all done"
    assert result.turn_count == 1


@pytest.mark.asyncio
async def test_loop_executes_tool_call(tmp_path, isolated_session_dir):
    (tmp_path / "f.txt").write_text("hello\n", encoding="utf-8")
    agent = _make_agent(
        tmp_path,
        llm=_ScriptedLLM(
            [
                LLMResponse(
                    text="",
                    tool_calls=[ToolCall(id="c1", name="read", arguments={"path": "f.txt"})],
                ),
                LLMResponse(text="contents acknowledged"),
            ]
        ),
    )
    result = await agent.run("read it")
    assert result.completed
    assert result.final_output == "contents acknowledged"
    types = [e.type for e in agent.session.read_events()]
    assert "tool_call_start" in types
    assert "tool_call_end" in types


@pytest.mark.asyncio
async def test_loop_max_turns(tmp_path, isolated_session_dir):
    agent = _make_agent(
        tmp_path,
        options=AgentOptions(model="fake", max_turns=2),
        llm=_ScriptedLLM(
            [
                LLMResponse(
                    tool_calls=[ToolCall(id="c1", name="read", arguments={"path": "missing.txt"})]
                ),
                LLMResponse(
                    tool_calls=[ToolCall(id="c2", name="read", arguments={"path": "missing.txt"})]
                ),
            ]
        ),
    )
    result = await agent.run("loop")
    assert not result.completed
    assert result.reason == "max_turns"


@pytest.mark.asyncio
async def test_steering_drained_before_turn(tmp_path, isolated_session_dir):
    """A steering message queued before the first turn is consumed by the
    loop's drain-queues step at the top of the turn."""

    agent = _make_agent(
        tmp_path,
        options=AgentOptions(model="fake", max_turns=2),
        llm=_ScriptedLLM([LLMResponse(text="received steering")]),
    )
    await agent._steering.put("change of plan")
    result = await agent.run("initial")
    assert result.completed
    events = agent.session.read_events()
    assert any(e.type == "steering_message" for e in events)


# ---------------------------------------------------------------------------
# (1) Terminate signal
# ---------------------------------------------------------------------------


class _DoneArgs(BaseModel):
    pass


class _DoneTool(Tool):
    """Returns ``terminate=True`` to short-circuit the next LLM call."""

    name = "done"
    description = "Mark the run as complete."
    args_schema = _DoneArgs

    async def execute(self, args, ctx):  # type: ignore[override]
        return ToolResult(content="acknowledged", terminate=True)


@pytest.mark.asyncio
async def test_terminate_skips_next_llm_call(tmp_path, isolated_session_dir):
    """A tool result with terminate=True ends the run after the tool batch
    without burning another LLM turn."""

    registry = ToolRegistry()
    registry.register(_DoneTool())
    session = Session.new(tmp_path, base_dir=tmp_path / "sessions")
    # Only ONE LLM response is scripted. If the loop didn't honor terminate
    # it would pop a second response and crash with IndexError.
    llm = _ScriptedLLM(
        [
            LLMResponse(
                text="signing off",
                tool_calls=[ToolCall(id="c1", name="done", arguments={})],
            ),
        ]
    )
    agent = Agent(
        AgentOptions(model="fake", max_turns=10),
        system_prompt="test",
        tool_registry=registry,
        session=session,
        event_bus=EventBus(),
        workspace=tmp_path,
        llm=llm,
    )
    result = await agent.run("go")
    assert result.completed
    assert result.reason == "completed"
    assert result.turn_count == 1


@pytest.mark.asyncio
async def test_mixed_terminate_batch_does_not_short_circuit(tmp_path, isolated_session_dir):
    """If only some tools in a batch set terminate, the loop must still
    call the LLM again."""

    registry = ToolRegistry()
    registry.register(_DoneTool())
    registry.register(_ReadTool())
    session = Session.new(tmp_path, base_dir=tmp_path / "sessions")
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    llm = _ScriptedLLM(
        [
            LLMResponse(
                text="batch",
                tool_calls=[
                    ToolCall(id="c1", name="done", arguments={}),
                    ToolCall(id="c2", name="read", arguments={"path": "f.txt"}),
                ],
            ),
            LLMResponse(text="now actually done"),
        ]
    )
    agent = Agent(
        AgentOptions(model="fake", max_turns=10),
        system_prompt="test",
        tool_registry=registry,
        session=session,
        event_bus=EventBus(),
        workspace=tmp_path,
        llm=llm,
    )
    result = await agent.run("go")
    assert result.completed
    assert result.final_output == "now actually done"
    assert result.turn_count == 2


# ---------------------------------------------------------------------------
# (2) Parallel tool dispatch
# ---------------------------------------------------------------------------


class _SlowReadArgs(BaseModel):
    path: str
    delay_ms: int = 50


class _SlowReadTool(Tool):
    """Sleeps before returning. Used to detect parallel execution by total
    wall time."""

    name = "slow_read"
    description = "Read with an artificial delay."
    args_schema = _SlowReadArgs

    async def execute(self, args, ctx):  # type: ignore[override]
        await asyncio.sleep(args.delay_ms / 1000)
        return f"read:{args.path}"


@pytest.mark.asyncio
async def test_parallel_tool_dispatch_runs_concurrently(tmp_path, isolated_session_dir):
    """Three 100ms tool calls run concurrently in ~100ms, not ~300ms."""

    registry = ToolRegistry()
    registry.register(_SlowReadTool())
    session = Session.new(tmp_path, base_dir=tmp_path / "sessions")
    llm = _ScriptedLLM(
        [
            LLMResponse(
                text="reading three",
                tool_calls=[
                    ToolCall(id="c1", name="slow_read", arguments={"path": "a", "delay_ms": 100}),
                    ToolCall(id="c2", name="slow_read", arguments={"path": "b", "delay_ms": 100}),
                    ToolCall(id="c3", name="slow_read", arguments={"path": "c", "delay_ms": 100}),
                ],
            ),
            LLMResponse(text="done"),
        ]
    )
    agent = Agent(
        AgentOptions(model="fake", max_turns=5, tool_execution="parallel"),
        system_prompt="test",
        tool_registry=registry,
        session=session,
        event_bus=EventBus(),
        workspace=tmp_path,
        llm=llm,
    )
    started = asyncio.get_event_loop().time()
    result = await agent.run("go")
    elapsed = asyncio.get_event_loop().time() - started
    assert result.completed
    # 3 x 100ms serial would be >0.28s; parallel should be <0.20s with
    # generous slack for fsync + event-bus overhead.
    assert elapsed < 0.20, f"expected parallel timing, got {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_parallel_persists_results_in_source_order(tmp_path, isolated_session_dir):
    """Tool messages must be appended in assistant source order even when
    completion order differs (b finishes before a)."""

    registry = ToolRegistry()
    registry.register(_SlowReadTool())
    session = Session.new(tmp_path, base_dir=tmp_path / "sessions")
    llm = _ScriptedLLM(
        [
            LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(id="ca", name="slow_read", arguments={"path": "a", "delay_ms": 80}),
                    ToolCall(id="cb", name="slow_read", arguments={"path": "b", "delay_ms": 10}),
                ],
            ),
            LLMResponse(text="done"),
        ]
    )
    agent = Agent(
        AgentOptions(model="fake", max_turns=5, tool_execution="parallel"),
        system_prompt="test",
        tool_registry=registry,
        session=session,
        event_bus=EventBus(),
        workspace=tmp_path,
        llm=llm,
    )
    await agent.run("go")
    msgs = agent._messages
    tool_msgs = [m for m in msgs if m.role == "tool"]
    assert [m.tool_call_id for m in tool_msgs] == ["ca", "cb"]


@pytest.mark.asyncio
async def test_sequential_tool_in_batch_forces_serialisation(tmp_path, isolated_session_dir):
    """If any tool in the batch is execution_mode='sequential', the whole
    batch runs sequentially even when global mode is parallel."""

    class _SequentialTool(Tool):
        name = "seq_read"
        description = "Sequential variant."
        args_schema = _SlowReadArgs
        execution_mode = "sequential"

        async def execute(self, args, ctx):  # type: ignore[override]
            await asyncio.sleep(args.delay_ms / 1000)
            return f"seq:{args.path}"

    registry = ToolRegistry()
    registry.register(_SequentialTool())
    session = Session.new(tmp_path, base_dir=tmp_path / "sessions")
    llm = _ScriptedLLM(
        [
            LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(id="c1", name="seq_read", arguments={"path": "a", "delay_ms": 80}),
                    ToolCall(id="c2", name="seq_read", arguments={"path": "b", "delay_ms": 80}),
                ],
            ),
            LLMResponse(text="done"),
        ]
    )
    agent = Agent(
        AgentOptions(model="fake", max_turns=5, tool_execution="parallel"),
        system_prompt="test",
        tool_registry=registry,
        session=session,
        event_bus=EventBus(),
        workspace=tmp_path,
        llm=llm,
    )
    started = asyncio.get_event_loop().time()
    await agent.run("go")
    elapsed = asyncio.get_event_loop().time() - started
    # Two 80ms tools serial should take >=0.15s.
    assert elapsed > 0.15, f"expected sequential timing, got {elapsed:.3f}s"


# ---------------------------------------------------------------------------
# (3) continue_run after error
# ---------------------------------------------------------------------------


class _BurstyLLM:
    """First call raises, then returns scripted responses."""

    def __init__(self, responses, fail_first: bool = True):
        self._responses = list(responses)
        self._fail_first = fail_first

    async def complete(self, **_):
        if self._fail_first:
            self._fail_first = False
            raise RuntimeError("simulated network error")
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_continue_run_after_llm_error(tmp_path, isolated_session_dir):
    """A run that ends in reason='error' can be continued without sending a
    new prompt; the retry succeeds with the same transcript."""

    registry = ToolRegistry()
    registry.register(_ReadTool())
    session = Session.new(tmp_path, base_dir=tmp_path / "sessions")
    llm = _BurstyLLM([LLMResponse(text="recovered after retry")])
    agent = Agent(
        AgentOptions(model="fake", max_turns=5),
        system_prompt="test",
        tool_registry=registry,
        session=session,
        event_bus=EventBus(),
        workspace=tmp_path,
        llm=llm,
    )

    first = await agent.run("go")
    assert not first.completed
    assert first.reason == "error"

    second = await agent.continue_run()
    assert second.completed
    assert second.final_output == "recovered after retry"


@pytest.mark.asyncio
async def test_continue_run_rejects_when_last_message_is_assistant(tmp_path, isolated_session_dir):
    """If the transcript ends in an assistant message, continue must refuse
    rather than send a malformed request."""

    registry = ToolRegistry()
    registry.register(_ReadTool())
    session = Session.new(tmp_path, base_dir=tmp_path / "sessions")
    llm = _ScriptedLLM([LLMResponse(text="all done")])
    agent = Agent(
        AgentOptions(model="fake", max_turns=5),
        system_prompt="test",
        tool_registry=registry,
        session=session,
        event_bus=EventBus(),
        workspace=tmp_path,
        llm=llm,
    )
    await agent.run("go")  # ends with assistant message

    with pytest.raises(ValueError, match="cannot continue from an assistant message"):
        await agent.continue_run()


# ---------------------------------------------------------------------------
# (4) Per-path mutation queue: writes parallelise across files,
# serialise within a single file.
# ---------------------------------------------------------------------------


class _SlowWriteArgs(BaseModel):
    path: str
    content: str
    delay_ms: int = 50


class _SlowWriteTool(Tool):
    """Like the production write tool but with an artificial sleep so the
    parallelism is timable. Uses ctx.extras['file_mutation_queue'] when
    present so we test the same code path."""

    name = "slow_write"
    description = "Write a file with an artificial delay."
    args_schema = _SlowWriteArgs

    async def execute(self, args, ctx):  # type: ignore[override]
        import contextlib

        target = ctx.workspace / args.path
        queue = ctx.extras.get("file_mutation_queue")
        guard = queue.acquire(target) if queue is not None else contextlib.nullcontext()
        async with guard:
            await asyncio.sleep(args.delay_ms / 1000)
            target.write_text(args.content, encoding="utf-8")
            return f"wrote:{args.path}"


@pytest.mark.asyncio
async def test_parallel_writes_to_different_files_run_concurrently(tmp_path, isolated_session_dir):
    """Two writes to *different* files must overlap when tool_execution
    is parallel — wall time roughly = single write, not 2x."""

    registry = ToolRegistry()
    registry.register(_SlowWriteTool())
    session = Session.new(tmp_path, base_dir=tmp_path / "sessions")
    llm = _ScriptedLLM(
        [
            LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="slow_write",
                        arguments={"path": "a.txt", "content": "A", "delay_ms": 100},
                    ),
                    ToolCall(
                        id="c2",
                        name="slow_write",
                        arguments={"path": "b.txt", "content": "B", "delay_ms": 100},
                    ),
                ],
            ),
            LLMResponse(text="done"),
        ]
    )
    agent = Agent(
        AgentOptions(model="fake", max_turns=5, tool_execution="parallel"),
        system_prompt="test",
        tool_registry=registry,
        session=session,
        event_bus=EventBus(),
        workspace=tmp_path,
        llm=llm,
    )
    started = asyncio.get_event_loop().time()
    result = await agent.run("go")
    elapsed = asyncio.get_event_loop().time() - started
    assert result.completed
    # Sequential would be ~0.2s+; parallel should be ~0.1s with slack.
    assert elapsed < 0.18, f"writes to different files did not parallelise (took {elapsed:.3f}s)"
    assert (tmp_path / "a.txt").read_text() == "A"
    assert (tmp_path / "b.txt").read_text() == "B"


@pytest.mark.asyncio
async def test_parallel_writes_to_same_file_serialise(tmp_path, isolated_session_dir):
    """Two writes to the *same* file must serialise even in parallel mode.
    The per-path lock guarantees the second write observes the first's
    completion (and the file ends with the second write's content)."""

    registry = ToolRegistry()
    registry.register(_SlowWriteTool())
    session = Session.new(tmp_path, base_dir=tmp_path / "sessions")
    llm = _ScriptedLLM(
        [
            LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="slow_write",
                        arguments={"path": "shared.txt", "content": "FIRST", "delay_ms": 80},
                    ),
                    ToolCall(
                        id="c2",
                        name="slow_write",
                        arguments={"path": "shared.txt", "content": "SECOND", "delay_ms": 80},
                    ),
                ],
            ),
            LLMResponse(text="done"),
        ]
    )
    agent = Agent(
        AgentOptions(model="fake", max_turns=5, tool_execution="parallel"),
        system_prompt="test",
        tool_registry=registry,
        session=session,
        event_bus=EventBus(),
        workspace=tmp_path,
        llm=llm,
    )
    started = asyncio.get_event_loop().time()
    result = await agent.run("go")
    elapsed = asyncio.get_event_loop().time() - started
    assert result.completed
    # Same-file serialisation: the two 80ms writes must NOT overlap.
    # Floor is ~0.16s; allow generous slack for fsync etc.
    assert elapsed >= 0.14, (
        f"writes to same file unexpectedly overlapped (took {elapsed:.3f}s — "
        f"per-path lock is broken)"
    )
    # Source order is preserved; second write wins.
    assert (tmp_path / "shared.txt").read_text() == "SECOND"


# ---------------------------------------------------------------------------
# (5) Dedup + circuit breaker end-to-end
# ---------------------------------------------------------------------------


class _CountingReadArgs(BaseModel):
    path: str


class _CountingReadTool(Tool):
    """Counts how many times execute() actually runs. Used to confirm
    dedup short-circuits before execution."""

    name = "read"
    description = "Read a file (test stub)."
    args_schema = _CountingReadArgs
    call_count = 0

    async def execute(self, args, ctx):  # type: ignore[override]
        type(self).call_count += 1
        target = ctx.workspace / args.path
        return target.read_text(encoding="utf-8") if target.is_file() else "(missing)"


@pytest.mark.asyncio
async def test_dedup_prevents_repeat_read_in_later_turn(tmp_path, isolated_session_dir):
    """Calling `read` with the same path on consecutive turns must hit
    the dedup, return the synthetic message, and NOT execute the tool
    a second time."""

    (tmp_path / "f.txt").write_text("hello", encoding="utf-8")
    _CountingReadTool.call_count = 0  # reset class-level counter

    registry = ToolRegistry()
    registry.register(_CountingReadTool())
    session = Session.new(tmp_path, base_dir=tmp_path / "sessions")
    llm = _ScriptedLLM(
        [
            LLMResponse(
                tool_calls=[ToolCall(id="c1", name="read", arguments={"path": "f.txt"})],
            ),
            LLMResponse(
                tool_calls=[ToolCall(id="c2", name="read", arguments={"path": "f.txt"})],
            ),
            LLMResponse(text="done"),
        ]
    )
    agent = Agent(
        AgentOptions(model="fake", max_turns=10),
        system_prompt="test",
        tool_registry=registry,
        session=session,
        event_bus=EventBus(),
        workspace=tmp_path,
        llm=llm,
    )
    result = await agent.run("read it twice")
    assert result.completed
    # First call ran. Second call dedup'd.
    assert _CountingReadTool.call_count == 1, (
        f"expected 1 actual execute() call (second call deduped); "
        f"got {_CountingReadTool.call_count}"
    )
    # The deduped tool message exists in the transcript with the
    # synthetic "duplicate call" content.
    msgs = [m for m in agent._messages if m.role == "tool" and m.tool_call_id == "c2"]
    assert msgs, "expected a tool message for the deduped second call"
    assert "duplicate call" in msgs[0].content


@pytest.mark.asyncio
async def test_dedup_disabled_runs_tool_twice(tmp_path, isolated_session_dir):
    """With tool_dedup_enabled=False, the same call runs twice."""

    (tmp_path / "f.txt").write_text("hello", encoding="utf-8")
    _CountingReadTool.call_count = 0

    registry = ToolRegistry()
    registry.register(_CountingReadTool())
    session = Session.new(tmp_path, base_dir=tmp_path / "sessions")
    llm = _ScriptedLLM(
        [
            LLMResponse(
                tool_calls=[ToolCall(id="c1", name="read", arguments={"path": "f.txt"})],
            ),
            LLMResponse(
                tool_calls=[ToolCall(id="c2", name="read", arguments={"path": "f.txt"})],
            ),
            LLMResponse(text="done"),
        ]
    )
    agent = Agent(
        AgentOptions(model="fake", max_turns=10, tool_dedup_enabled=False),
        system_prompt="test",
        tool_registry=registry,
        session=session,
        event_bus=EventBus(),
        workspace=tmp_path,
        llm=llm,
    )
    await agent.run("read it twice")
    assert _CountingReadTool.call_count == 2


class _FailingFetchArgs(BaseModel):
    url: str


class _FailingFetchTool(Tool):
    """Always raises ToolError. Used to feed the breaker consecutive
    failures."""

    name = "web_fetch"
    description = "Fetch a URL (always fails)."
    args_schema = _FailingFetchArgs
    call_count = 0

    async def execute(self, args, ctx):  # type: ignore[override]
        type(self).call_count += 1
        from pyharness import ToolError

        raise ToolError(f"404 Not Found: {args.url}")


@pytest.mark.asyncio
async def test_breaker_opens_after_threshold_and_short_circuits(tmp_path, isolated_session_dir):
    """Three consecutive web_fetch failures open the breaker. A 4th
    call within cooldown is refused without executing the tool."""

    _FailingFetchTool.call_count = 0
    registry = ToolRegistry()
    registry.register(_FailingFetchTool())
    session = Session.new(tmp_path, base_dir=tmp_path / "sessions")
    llm = _ScriptedLLM(
        [
            LLMResponse(
                tool_calls=[ToolCall(id="c1", name="web_fetch", arguments={"url": "https://a"})],
            ),
            LLMResponse(
                tool_calls=[ToolCall(id="c2", name="web_fetch", arguments={"url": "https://b"})],
            ),
            LLMResponse(
                tool_calls=[ToolCall(id="c3", name="web_fetch", arguments={"url": "https://c"})],
            ),
            LLMResponse(
                tool_calls=[ToolCall(id="c4", name="web_fetch", arguments={"url": "https://d"})],
            ),
            LLMResponse(text="giving up"),
        ]
    )
    agent = Agent(
        AgentOptions(
            model="fake",
            max_turns=10,
            web_fetch_failure_threshold=3,
            web_fetch_cooldown_turns=5,
        ),
        system_prompt="test",
        tool_registry=registry,
        session=session,
        event_bus=EventBus(),
        workspace=tmp_path,
        llm=llm,
    )
    result = await agent.run("try fetching")
    assert result.completed
    # Three actual failures executed; the fourth must be short-circuited.
    assert _FailingFetchTool.call_count == 3, (
        f"expected 3 real fetches (threshold) before breaker opens; "
        f"got {_FailingFetchTool.call_count}"
    )
    # The 4th tool call has the breaker's synthetic content in the transcript.
    breaker_msgs = [m for m in agent._messages if m.role == "tool" and m.tool_call_id == "c4"]
    assert breaker_msgs, "expected a tool message for the refused 4th call"
    assert "circuit breaker" in breaker_msgs[0].content
