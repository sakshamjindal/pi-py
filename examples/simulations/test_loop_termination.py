"""Loop termination paths: max_turns, abort, mid-run steering."""

from __future__ import annotations

import asyncio

import pytest

from coding_harness import CodingAgent, CodingAgentConfig, Settings
from pyharness import LLMResponse, ToolCall

from ._helpers import install_scripted_llm, make_project


@pytest.mark.asyncio
async def test_max_turns(tmp_path, isolated_session_dir):
    workspace = make_project(tmp_path)
    agent = CodingAgent(CodingAgentConfig(workspace=workspace, settings=Settings(), max_turns=2))
    install_scripted_llm(
        agent,
        [
            LLMResponse(tool_calls=[ToolCall(id=f"c{i}", name="read", arguments={"path": "x"})])
            for i in range(10)
        ],
    )
    result = await agent.run("loop forever")
    assert result.completed is False
    assert result.reason == "max_turns"
    assert result.turn_count == 2


@pytest.mark.asyncio
async def test_abort(tmp_path, isolated_session_dir):
    workspace = make_project(tmp_path)
    agent = CodingAgent(CodingAgentConfig(workspace=workspace, settings=Settings()))

    async def _slow(**_):
        await asyncio.sleep(0.05)
        return LLMResponse(tool_calls=[ToolCall(id="c1", name="read", arguments={"path": "f"})])

    agent.llm.complete = _slow  # type: ignore[assignment]

    handle = agent.start("forever")
    await asyncio.sleep(0.02)
    handle.abort_event.set()
    result = await handle.wait()
    assert result.reason == "aborted"


@pytest.mark.asyncio
async def test_steering_message_delivered_at_next_turn(tmp_path, isolated_session_dir):
    """Push steering during turn 1; assert it lands in turn 2's messages.
    Uses asyncio.Events to pin the timing deterministically."""

    workspace = make_project(tmp_path)
    agent = CodingAgent(CodingAgentConfig(workspace=workspace, settings=Settings()))

    seen_messages: list[list] = []
    turn_1_in_llm = asyncio.Event()
    release_turn_1 = asyncio.Event()

    async def _capture(**kwargs):
        seen_messages.append(list(kwargs.get("messages", [])))
        if len(seen_messages) == 1:
            turn_1_in_llm.set()
            await release_turn_1.wait()
            return LLMResponse(tool_calls=[ToolCall(id="c1", name="read", arguments={"path": "x"})])
        return LLMResponse(text="done")

    agent.llm.complete = _capture  # type: ignore[assignment]

    handle = agent.start("first")
    await turn_1_in_llm.wait()
    await handle.steer("intervention")
    release_turn_1.set()

    result = await handle.wait()
    assert result.completed is True

    turn_2_msgs = seen_messages[1] if len(seen_messages) > 1 else []
    rendered = " ".join(str(m.content) for m in turn_2_msgs)
    assert "intervention" in rendered, f"steering not delivered. Turn 2: {rendered!r}"
