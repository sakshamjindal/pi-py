"""End-to-end tests for the agent loop with a fake LLM client."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pyharness.harness import Harness, HarnessConfig
from pyharness.types import LLMResponse, Message, ToolCall


class _ScriptedLLM:
    """Returns prepared responses in order, ignoring inputs."""

    def __init__(self, responses):
        self._responses = list(responses)

    async def complete(self, **_):
        return self._responses.pop(0)

    async def stream(self, **_):
        # Not used by the harness when complete() is called directly.
        if False:
            yield None


@pytest.mark.asyncio
async def test_loop_terminates_on_no_tool_calls(tmp_path, monkeypatch, isolated_session_dir):
    cfg = HarnessConfig(workspace=tmp_path, model="fake", bare=True)
    harness = Harness(cfg)
    harness.llm = _ScriptedLLM([LLMResponse(text="all done")])

    result = await harness.run("hello")
    assert result.completed
    assert result.final_output == "all done"
    assert result.turn_count == 1


@pytest.mark.asyncio
async def test_loop_executes_tool_call(tmp_path, isolated_session_dir):
    (tmp_path / "f.txt").write_text("hello\n", encoding="utf-8")

    cfg = HarnessConfig(workspace=tmp_path, model="fake", bare=True)
    harness = Harness(cfg)
    # First call triggers a `read` tool; second call ends.
    harness.llm = _ScriptedLLM(
        [
            LLMResponse(
                text="",
                tool_calls=[ToolCall(id="c1", name="read", arguments={"path": "f.txt"})],
            ),
            LLMResponse(text="contents acknowledged"),
        ]
    )

    result = await harness.run("read it")
    assert result.completed
    assert result.final_output == "contents acknowledged"
    # Session log must have a tool start/end pair.
    events = harness.session.read_events()
    types = [e.type for e in events]
    assert "tool_call_start" in types
    assert "tool_call_end" in types


@pytest.mark.asyncio
async def test_loop_max_turns(tmp_path, isolated_session_dir):
    cfg = HarnessConfig(workspace=tmp_path, model="fake", bare=True, max_turns=2)
    harness = Harness(cfg)
    harness.llm = _ScriptedLLM(
        [
            LLMResponse(
                tool_calls=[ToolCall(id="c1", name="read", arguments={"path": "missing.txt"})]
            ),
            LLMResponse(
                tool_calls=[ToolCall(id="c2", name="read", arguments={"path": "missing.txt"})]
            ),
        ]
    )
    result = await harness.run("loop")
    assert not result.completed
    assert result.reason == "max_turns"


@pytest.mark.asyncio
async def test_steering_drained_before_turn(tmp_path, isolated_session_dir):
    """A steering message queued before the first turn is consumed by the
    loop's drain-queues step at the top of the turn."""

    cfg = HarnessConfig(workspace=tmp_path, model="fake", bare=True, max_turns=2)
    harness = Harness(cfg)
    harness.llm = _ScriptedLLM([LLMResponse(text="received steering")])

    await harness._steering.put("change of plan")
    result = await harness.run("initial")
    assert result.completed
    events = harness.session.read_events()
    assert any(e.type == "steering_message" for e in events)
