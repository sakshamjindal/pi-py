"""LLM-side failure scenarios (mock mode)."""

from __future__ import annotations

import pytest

from coding_harness import CodingAgent, CodingAgentConfig, Settings
from pyharness import LLMResponse, ToolCall

from ._helpers import install_raising_llm, install_scripted_llm, make_project


@pytest.mark.asyncio
async def test_llm_raises_returns_error_result(tmp_path, isolated_session_dir):
    """LLM exception must surface as RunResult(reason='error') with the
    exception text preserved — the loop must NOT crash."""

    workspace = make_project(tmp_path)
    agent = CodingAgent(CodingAgentConfig(workspace=workspace, settings=Settings()))
    install_raising_llm(agent, RuntimeError("synthetic LLM failure"))

    result = await agent.run("hello")
    assert result.completed is False
    assert result.reason == "error"
    assert "synthetic LLM failure" in (result.final_output or "")


@pytest.mark.asyncio
async def test_llm_no_tool_calls_completes(tmp_path, isolated_session_dir):
    workspace = make_project(tmp_path)
    agent = CodingAgent(CodingAgentConfig(workspace=workspace, settings=Settings()))
    install_scripted_llm(agent, [LLMResponse(text="task complete")])
    result = await agent.run("anything")
    assert result.completed is True
    assert result.reason == "completed"
    assert result.final_output == "task complete"


@pytest.mark.asyncio
async def test_unknown_tool_call_does_not_crash(tmp_path, isolated_session_dir):
    """Unknown tool name in tool_calls must produce a graceful error
    tool result the model can recover from."""

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
    assert result.completed is True
    assert result.final_output == "ok i'll stop"


@pytest.mark.asyncio
async def test_scripted_llm_exhaustion_surfaces_as_error(tmp_path, isolated_session_dir):
    """Sanity: an empty script produces a clean error, not a test-harness crash."""

    workspace = make_project(tmp_path)
    agent = CodingAgent(CodingAgentConfig(workspace=workspace, settings=Settings()))
    install_scripted_llm(agent, [])
    result = await agent.run("hello")
    assert result.reason == "error"
    assert "exhausted" in (result.final_output or "").lower()
