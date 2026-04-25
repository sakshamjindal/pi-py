"""End-to-end tests for the agent loop with a fake LLM client."""

from __future__ import annotations

import asyncio

import pytest

from pyharness import (
    Agent,
    AgentOptions,
    EventBus,
    LLMResponse,
    Session,
    Tool,
    ToolContext,
    ToolRegistry,
    ToolCall,
)
from pydantic import BaseModel, Field


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
