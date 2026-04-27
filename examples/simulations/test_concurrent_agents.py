"""Concurrent agents — the SDK's headline promise."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from coding_harness import CodingAgent, CodingAgentConfig, Settings
from pyharness import LLMResponse, Tool

from ._helpers import install_scripted_llm, make_project


@pytest.mark.asyncio
async def test_two_agents_run_concurrently(tmp_path, isolated_session_dir):
    ws_a = make_project(tmp_path / "agent-a")
    ws_b = make_project(tmp_path / "agent-b")
    a = CodingAgent(CodingAgentConfig(workspace=ws_a, settings=Settings()))
    b = CodingAgent(CodingAgentConfig(workspace=ws_b, settings=Settings()))
    install_scripted_llm(a, [LLMResponse(text="A done")])
    install_scripted_llm(b, [LLMResponse(text="B done")])
    res_a, res_b = await asyncio.gather(a.run("task A"), b.run("task B"))
    assert res_a.completed and res_a.final_output == "A done"
    assert res_b.completed and res_b.final_output == "B done"
    assert res_a.session_id != res_b.session_id


@pytest.mark.asyncio
async def test_independent_tool_registries(tmp_path, isolated_session_dir):
    """A custom tool on agent A must NOT appear on agent B."""

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
    a = CodingAgent(CodingAgentConfig(workspace=ws_a, settings=Settings(), extra_tools=[TagTool()]))
    b = CodingAgent(CodingAgentConfig(workspace=ws_b, settings=Settings()))
    assert a.tool_registry.has("tag_a_only")
    assert not b.tool_registry.has("tag_a_only")
