"""Compactor reduces messages and emits a summary marker."""

from __future__ import annotations

from typing import Any

import pytest

from pyharness.compaction import Compactor
from pyharness.types import LLMResponse, Message


class _FakeLLM:
    async def complete(self, *, model, messages, **_):
        return LLMResponse(text="SUMMARY")


@pytest.mark.asyncio
async def test_compaction_keeps_recent_and_summarises_middle():
    llm = _FakeLLM()
    compactor = Compactor(llm, summarization_model="x", keep_recent_count=2)
    msgs = [Message(role="system", content="sys")]
    for i in range(10):
        msgs.append(Message(role="user", content=f"u{i}" * 200))
        msgs.append(Message(role="assistant", content=f"a{i}" * 200))

    result = await compactor.maybe_compact(msgs, threshold_tokens=100, model_for_count="x")
    assert result.compacted
    assert result.tokens_after < result.tokens_before
    # System preserved as first message; summary marker injected next.
    assert result.messages[0].role == "system"
    assert "compacted" in str(result.messages[1].content).lower()
    assert "SUMMARY" in str(result.messages[1].content)


@pytest.mark.asyncio
async def test_no_compaction_below_threshold():
    llm = _FakeLLM()
    compactor = Compactor(llm, summarization_model="x", keep_recent_count=2)
    msgs = [Message(role="system", content="sys"), Message(role="user", content="hi")]
    result = await compactor.maybe_compact(msgs, threshold_tokens=10_000, model_for_count="x")
    assert not result.compacted
    assert result.messages == msgs
