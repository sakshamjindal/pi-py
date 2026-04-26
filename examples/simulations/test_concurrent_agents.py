"""Concurrent agents in one process — the SDK's headline promise.

Two agents with different workspaces running under ``asyncio.gather``
must complete independently without sharing tool registries, sessions,
or LLM mocks.
"""

from __future__ import annotations

import asyncio

import pytest

from coding_harness import CodingAgent, CodingAgentConfig, Settings
from pyharness import LLMResponse

from ._helpers import install_scripted_llm, make_project


@pytest.mark.asyncio
async def test_two_agents_run_concurrently_with_separate_workspaces(tmp_path, isolated_session_dir):
    ws_a = make_project(tmp_path / "agent-a")
    ws_b = make_project(tmp_path / "agent-b")

    agent_a = CodingAgent(CodingAgentConfig(workspace=ws_a, settings=Settings()))
    agent_b = CodingAgent(CodingAgentConfig(workspace=ws_b, settings=Settings()))

    install_scripted_llm(agent_a, [LLMResponse(text="A done")])
    install_scripted_llm(agent_b, [LLMResponse(text="B done")])

    res_a, res_b = await asyncio.gather(agent_a.run("task A"), agent_b.run("task B"))

    assert res_a.completed is True
    assert res_a.final_output == "A done"
    assert res_b.completed is True
    assert res_b.final_output == "B done"
    # Different sessions.
    assert res_a.session_id != res_b.session_id


@pytest.mark.asyncio
async def test_concurrent_agents_have_independent_tool_registries(tmp_path, isolated_session_dir):
    """A custom tool registered on agent A must NOT appear on agent B."""

    from pydantic import BaseModel

    from pyharness import Tool

    class _Args(BaseModel):
        pass

    class TagTool(Tool):
        name = "tag_a_only"
        description = "Only on A."
        args_schema = _Args

        async def execute(self, args, ctx):
            return "tagged"

    ws_a = make_project(tmp_path / "agent-a")
    ws_b = make_project(tmp_path / "agent-b")

    agent_a = CodingAgent(
        CodingAgentConfig(workspace=ws_a, settings=Settings(), extra_tools=[TagTool()])
    )
    agent_b = CodingAgent(CodingAgentConfig(workspace=ws_b, settings=Settings()))

    assert agent_a.tool_registry.has("tag_a_only")
    assert not agent_b.tool_registry.has("tag_a_only")
