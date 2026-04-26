"""Loop termination paths.

The loop exits via one of:
- ``completed`` — LLM returns no tool_calls
- ``max_turns`` — turn counter exhausted
- ``aborted`` — ``AgentHandle.abort_event`` set
- ``error`` — LLM exception or extension Deny on ``before_llm_call``

Plus mid-run steering / follow-up message delivery.
"""

from __future__ import annotations

import asyncio

import pytest

from coding_harness import CodingAgent, CodingAgentConfig, Settings
from pyharness import LLMResponse, ToolCall

from ._helpers import install_scripted_llm, make_project


@pytest.mark.asyncio
async def test_max_turns_terminates_with_correct_reason(tmp_path, isolated_session_dir):
    """If the LLM never stops asking for tool calls, the loop must
    exit at max_turns with the correct reason."""

    workspace = make_project(tmp_path)
    agent = CodingAgent(
        CodingAgentConfig(
            workspace=workspace,
            settings=Settings(),
            max_turns=2,
        )
    )
    # An infinite loop of read calls — script enough responses for the
    # bounded run.
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
async def test_abort_event_terminates_run(tmp_path, isolated_session_dir):
    """Calling ``handle.abort_event.set()`` mid-run must cause the
    loop to exit with reason='aborted'."""

    workspace = make_project(tmp_path)
    agent = CodingAgent(CodingAgentConfig(workspace=workspace, settings=Settings()))

    # Slow-ish script: each LLM call awaits to give us time to abort.
    async def _complete(**_):
        await asyncio.sleep(0.05)
        return LLMResponse(tool_calls=[ToolCall(id="c1", name="read", arguments={"path": "f"})])

    agent.llm.complete = _complete  # type: ignore[assignment]

    handle = agent.start("forever")
    await asyncio.sleep(0.02)
    handle.abort_event.set()
    result = await handle.wait()
    assert result.reason == "aborted"


@pytest.mark.asyncio
async def test_steering_message_delivered_at_next_turn(tmp_path, isolated_session_dir):
    """A steering message pushed via ``handle.steer()`` must reach the
    LLM via the messages list on the next turn.

    Uses an asyncio.Event to synchronise: the second LLM call waits on
    the event so the test has a guaranteed window to push the steering
    message between turn 1's drain and turn 2's drain.
    """

    workspace = make_project(tmp_path)
    agent = CodingAgent(CodingAgentConfig(workspace=workspace, settings=Settings()))

    # The loop drains the steering queue at the TOP of each turn, before
    # the LLM call. So for steering to be visible to the LLM at turn N,
    # it has to be pushed before turn N's drain happens. We block turn 1
    # inside the LLM call, push steering, release — turn 1 finishes with
    # a tool_call, tool runs, turn 2 drains (picks up steering), turn 2's
    # LLM call sees it.

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

    # Turn 2's messages must contain the steering text.
    turn_2_msgs = seen_messages[1] if len(seen_messages) > 1 else []
    rendered = " ".join(str(m.content) for m in turn_2_msgs)
    assert "intervention" in rendered, f"steering not delivered. Turn 2 messages: {rendered!r}"
