"""Compaction scenarios.

Compaction is supposed to be transparent: summarise the middle of the
transcript, keep the head + tail, return the same shape. If
summarisation fails, the loop should fail gracefully (reason='error')
rather than crash the run with an unhandled exception.
"""

from __future__ import annotations

import pytest

from coding_harness import CodingAgent, CodingAgentConfig, Settings
from pyharness import LLMResponse

from ._helpers import make_project


@pytest.mark.asyncio
async def test_compaction_summarisation_failure_returns_clean_error(tmp_path, isolated_session_dir):
    """If the summarisation LLM raises, the run must not crash with an
    unhandled exception — it should return a clean RunResult with
    reason='error'."""

    workspace = make_project(tmp_path)
    # Force compaction to trigger by making the threshold tiny and
    # padding the conversation with synthetic prior messages via
    # extra_messages. Combined with a message count past the
    # keep_recent_count + 1 floor, this drives maybe_compact down the
    # actual summarisation path on turn 1.

    # We use a very small context window so the threshold * pct is < the
    # initial system prompt + user message + extra_messages combined.
    settings = Settings(
        model_context_window=200,
        compaction_threshold_pct=0.5,  # threshold = 100 tokens
        keep_recent_count=1,
    )

    from pyharness import Message

    # 5 padding messages to comfortably exceed keep_recent_count + 1 = 2.
    extra = [
        Message(role="user" if i % 2 == 0 else "assistant", content="x" * 200) for i in range(5)
    ]

    agent = CodingAgent(
        CodingAgentConfig(
            workspace=workspace,
            settings=settings,
            extra_messages=extra,
        )
    )

    # Make BOTH the summariser and the main LLM raise for the
    # summariser. The trick: when the summariser is called, its model
    # is settings.summarization_model — different from the main model.
    # We replace `complete` so all calls raise.

    async def _failing(**_):
        raise RuntimeError("summariser exploded")

    agent.llm.complete = _failing  # type: ignore[assignment]

    result = await agent.run("trigger compaction")
    # The run must not crash; it must surface as an error result.
    assert result.completed is False
    assert result.reason == "error"
    assert (
        "exploded" in (result.final_output or "") or "error" in (result.final_output or "").lower()
    )


@pytest.mark.asyncio
async def test_no_compaction_when_under_threshold(tmp_path, isolated_session_dir):
    """If the message count and tokens are under the compaction
    threshold, no compaction occurs and the agent runs normally."""

    workspace = make_project(tmp_path)
    agent = CodingAgent(CodingAgentConfig(workspace=workspace, settings=Settings()))

    async def _ok(**_):
        return LLMResponse(text="done")

    agent.llm.complete = _ok  # type: ignore[assignment]

    result = await agent.run("hi")
    assert result.completed is True
    # Compaction event count: should be zero in the session log.
    types = [e.type for e in agent.session.read_events()]
    assert "compaction" not in types
