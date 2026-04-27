"""Compaction scenarios — regression for PR #11 bug + happy path."""

from __future__ import annotations

import pytest

from coding_harness import CodingAgent, CodingAgentConfig, Settings
from pyharness import LLMResponse, Message

from ._helpers import make_project


@pytest.mark.asyncio
async def test_compaction_summariser_failure_returns_clean_error(tmp_path, isolated_session_dir):
    """REGRESSION for the PR #11 bug. If the summariser raises, the run
    must NOT crash with an unhandled exception — surface as
    reason='error'. Pre-PR-#11 this would propagate out of the loop."""

    workspace = make_project(tmp_path)
    settings = Settings(model_context_window=200, compaction_threshold_pct=0.5, keep_recent_count=1)
    extra = [
        Message(role="user" if i % 2 == 0 else "assistant", content="x" * 200) for i in range(5)
    ]
    agent = CodingAgent(
        CodingAgentConfig(workspace=workspace, settings=settings, extra_messages=extra)
    )

    async def _failing(**_):
        raise RuntimeError("summariser exploded")

    agent.llm.complete = _failing  # type: ignore[assignment]

    result = await agent.run("trigger compaction")
    # Post-fix behaviour: compaction failure logs + continues with original
    # messages; downstream LLM call also fails (same _failing fn) and
    # surfaces as reason='error' through the existing catch.
    assert result.completed is False
    assert result.reason == "error"


@pytest.mark.asyncio
async def test_no_compaction_under_threshold(tmp_path, isolated_session_dir):
    workspace = make_project(tmp_path)
    agent = CodingAgent(CodingAgentConfig(workspace=workspace, settings=Settings()))

    async def _ok(**_):
        return LLMResponse(text="done")

    agent.llm.complete = _ok  # type: ignore[assignment]

    result = await agent.run("hi")
    assert result.completed is True
    types = [e.type for e in agent.session.read_events()]
    assert "compaction" not in types
