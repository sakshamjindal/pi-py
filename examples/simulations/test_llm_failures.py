"""LLM-side failure scenarios.

Verifies the loop's behaviour when the LLM client misbehaves: raises
mid-run, returns an exhausted script, or emits an unknown tool call.
"""

from __future__ import annotations

import pytest

from coding_harness import CodingAgent, CodingAgentConfig, Settings
from pyharness import LLMResponse, ToolCall

from ._helpers import install_raising_llm, install_scripted_llm, make_project


@pytest.mark.asyncio
async def test_llm_raises_mid_run_returns_error_result(tmp_path, isolated_session_dir):
    """``RunResult.reason`` must be ``"error"`` and the message must
    carry the exception text when the LLM raises."""

    workspace = make_project(tmp_path)
    agent = CodingAgent(CodingAgentConfig(workspace=workspace, settings=Settings()))
    install_raising_llm(agent, RuntimeError("synthetic LLM failure"))

    result = await agent.run("hello")
    assert result.completed is False
    assert result.reason == "error"
    assert "synthetic LLM failure" in (result.final_output or "")


@pytest.mark.asyncio
async def test_llm_returns_text_with_no_tool_calls_completes(tmp_path, isolated_session_dir):
    """The simplest happy path: model says 'done', loop exits cleanly."""

    workspace = make_project(tmp_path)
    agent = CodingAgent(CodingAgentConfig(workspace=workspace, settings=Settings()))
    install_scripted_llm(agent, [LLMResponse(text="task complete")])

    result = await agent.run("anything")
    assert result.completed is True
    assert result.reason == "completed"
    assert result.final_output == "task complete"


@pytest.mark.asyncio
async def test_llm_returns_unknown_tool_call_does_not_crash(tmp_path, isolated_session_dir):
    """A tool_call with an unregistered name must produce a graceful
    error tool result the model can recover from — the loop must not
    crash."""

    workspace = make_project(tmp_path)
    agent = CodingAgent(CodingAgentConfig(workspace=workspace, settings=Settings()))
    install_scripted_llm(
        agent,
        [
            LLMResponse(
                text="",
                tool_calls=[ToolCall(id="c1", name="not_a_real_tool", arguments={"x": 1})],
            ),
            LLMResponse(text="ok i'll stop"),
        ],
    )
    result = await agent.run("call a fake tool")
    # Must reach the second response — proves the loop survived the
    # unknown tool call.
    assert result.completed is True
    assert result.final_output == "ok i'll stop"


@pytest.mark.asyncio
async def test_scripted_llm_exhaustion_surfaces_as_error(tmp_path, isolated_session_dir):
    """Sanity: a script too short for the run produces a clean error
    result instead of crashing the test harness."""

    workspace = make_project(tmp_path)
    agent = CodingAgent(CodingAgentConfig(workspace=workspace, settings=Settings()))
    install_scripted_llm(agent, [])  # zero responses

    result = await agent.run("hello")
    assert result.reason == "error"
    assert "exhausted" in (result.final_output or "").lower()
