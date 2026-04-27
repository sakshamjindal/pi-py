"""Compactor reduces messages and emits a summary marker."""

from __future__ import annotations

from typing import Any

import pytest

from pyharness.compaction import Compactor
from pyharness.types import LLMResponse, Message


class _FakeLLM:
    """Stub LLM that records the kwargs of each `complete()` call so
    tests can assert what was actually requested (e.g. max_tokens)."""

    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
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


@pytest.mark.asyncio
async def test_compactor_caps_summary_max_tokens():
    """The Compactor must pass max_tokens to the summarisation LLM call.
    Without this, providers like OpenRouter reserve credit against the
    model's default (64k for Haiku/Opus 4.x), which means a routine
    compaction requires ~$5 of pre-paid balance even though actual
    summaries are <2k tokens. This is the regression test for that
    bug."""

    llm = _FakeLLM()
    compactor = Compactor(
        llm,
        summarization_model="x",
        keep_recent_count=2,
        max_summary_tokens=4_000,
    )
    msgs = [Message(role="system", content="sys")]
    for i in range(6):
        msgs.append(Message(role="user", content=f"u{i}" * 200))
        msgs.append(Message(role="assistant", content=f"a{i}" * 200))

    await compactor.maybe_compact(msgs, threshold_tokens=100, model_for_count="x")
    assert llm.calls, "compactor should have called the summarisation LLM"
    call = llm.calls[0]
    assert call.get("max_tokens") == 4_000, (
        f"summarisation call must pass max_tokens (the cap on output tokens). "
        f"Got: {call.get('max_tokens')!r}"
    )


@pytest.mark.asyncio
async def test_compactor_default_max_summary_tokens_is_set():
    """If the constructor isn't given max_summary_tokens, a sane default
    is still passed (not None / not omitted)."""

    llm = _FakeLLM()
    compactor = Compactor(llm, summarization_model="x", keep_recent_count=2)
    msgs = [Message(role="system", content="sys")]
    for i in range(6):
        msgs.append(Message(role="user", content=f"u{i}" * 200))
        msgs.append(Message(role="assistant", content=f"a{i}" * 200))
    await compactor.maybe_compact(msgs, threshold_tokens=100, model_for_count="x")
    assert llm.calls
    assert llm.calls[0].get("max_tokens") is not None
    # Default should be a reasonable cap for any modern LLM (≥1k, ≤32k).
    cap = llm.calls[0]["max_tokens"]
    assert 1_000 <= cap <= 32_000, f"unreasonable default max_tokens: {cap}"
