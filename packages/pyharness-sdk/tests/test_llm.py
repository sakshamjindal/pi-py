"""LLM client tests using a fake LiteLLM acompletion."""

from __future__ import annotations

import json
from typing import Any

import pytest

from pyharness.llm import LLMClient
from pyharness.types import Message


class _FakeChunk:
    """Minimal chunk shape: choices with deltas and optional usage."""

    def __init__(self, choices, usage=None):
        self.choices = choices
        self.usage = usage


class _FakeChoice:
    def __init__(self, delta, finish_reason=None):
        self.delta = delta
        self.finish_reason = finish_reason


class _FakeDelta:
    def __init__(self, content=None, tool_calls=None, thinking=None, reasoning_content=None):
        self.content = content
        self.tool_calls = tool_calls
        self.thinking = thinking
        self.reasoning_content = reasoning_content


class _FakeToolCall:
    def __init__(self, *, idx, tcid, name=None, args=None):
        self.index = idx
        self.id = tcid
        self.function = type("F", (), {"name": name, "arguments": args})()


async def _fake_acompletion_text(**kwargs):
    """Stream that emits two text deltas then stops."""

    async def gen():
        yield _FakeChunk([_FakeChoice(_FakeDelta(content="Hello "))])
        yield _FakeChunk([_FakeChoice(_FakeDelta(content="world"))])
        yield _FakeChunk(
            [_FakeChoice(_FakeDelta(content=None), finish_reason="stop")],
            usage={"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
        )

    return gen()


async def _fake_acompletion_tool(**kwargs):
    async def gen():
        yield _FakeChunk(
            [
                _FakeChoice(
                    _FakeDelta(
                        tool_calls=[_FakeToolCall(idx=0, tcid="call_1", name="read", args="")]
                    )
                )
            ]
        )
        yield _FakeChunk(
            [
                _FakeChoice(
                    _FakeDelta(tool_calls=[_FakeToolCall(idx=0, tcid="call_1", args='{"path":')])
                )
            ]
        )
        yield _FakeChunk(
            [
                _FakeChoice(
                    _FakeDelta(tool_calls=[_FakeToolCall(idx=0, tcid="call_1", args='"a.txt"}')]),
                    finish_reason="tool_calls",
                )
            ],
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )

    return gen()


@pytest.mark.asyncio
async def test_complete_text(monkeypatch):
    import pyharness.llm as llm_mod

    monkeypatch.setattr("litellm.acompletion", _fake_acompletion_text, raising=False)

    client = LLMClient()
    response = await client.complete(
        model="claude-haiku-4-5",
        messages=[Message(role="user", content="hi")],
    )
    assert response.text == "Hello world"
    assert response.tool_calls == []
    assert response.usage.total_tokens == 7
    assert response.finish_reason == "stop"


@pytest.mark.asyncio
async def test_complete_tool_call(monkeypatch):
    monkeypatch.setattr("litellm.acompletion", _fake_acompletion_tool, raising=False)

    client = LLMClient()
    response = await client.complete(
        model="claude-haiku-4-5",
        messages=[Message(role="user", content="read a.txt")],
        tools=[{"type": "function", "function": {"name": "read"}}],
    )
    assert len(response.tool_calls) == 1
    tc = response.tool_calls[0]
    assert tc.name == "read"
    assert tc.arguments == {"path": "a.txt"}


@pytest.mark.asyncio
async def test_streaming_yields_events(monkeypatch):
    monkeypatch.setattr("litellm.acompletion", _fake_acompletion_text, raising=False)
    client = LLMClient()
    types = []
    async for ev in client.stream(
        model="claude-haiku-4-5",
        messages=[Message(role="user", content="hi")],
    ):
        types.append(ev.type)
    assert "text_delta" in types
    assert "message_stop" in types


# ---------------------------------------------------------------------------
# Thinking / extended-reasoning capture (regression: previously the chunks
# were emitted by the provider but pyharness silently dropped them).
# ---------------------------------------------------------------------------


async def _fake_acompletion_anthropic_thinking(**kwargs):
    """Mirror the shape Anthropic returns when extended thinking is on:
    ``delta.thinking`` carries the reasoning chunks; ``delta.content``
    carries the final text."""

    async def gen():
        yield _FakeChunk([_FakeChoice(_FakeDelta(thinking="Let me reason. "))])
        yield _FakeChunk([_FakeChoice(_FakeDelta(thinking="Step 1: read. "))])
        yield _FakeChunk([_FakeChoice(_FakeDelta(thinking="Step 2: reply."))])
        yield _FakeChunk([_FakeChoice(_FakeDelta(content="The answer is 42."))])
        yield _FakeChunk(
            [_FakeChoice(_FakeDelta(content=None), finish_reason="stop")],
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )

    return gen()


async def _fake_acompletion_openai_reasoning(**kwargs):
    """Mirror the shape OpenAI o1-class models return:
    ``delta.reasoning_content`` carries the reasoning."""

    async def gen():
        yield _FakeChunk([_FakeChoice(_FakeDelta(reasoning_content="Reasoning chunk one. "))])
        yield _FakeChunk([_FakeChoice(_FakeDelta(reasoning_content="Reasoning chunk two."))])
        yield _FakeChunk([_FakeChoice(_FakeDelta(content="Final reply."))])
        yield _FakeChunk(
            [_FakeChoice(_FakeDelta(content=None), finish_reason="stop")],
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )

    return gen()


@pytest.mark.asyncio
async def test_complete_captures_anthropic_thinking(monkeypatch):
    """Anthropic-shape thinking chunks must accumulate into LLMResponse.thinking
    and not bleed into LLMResponse.text."""

    monkeypatch.setattr("litellm.acompletion", _fake_acompletion_anthropic_thinking, raising=False)
    client = LLMClient()
    response = await client.complete(
        model="claude-opus-4-7",
        messages=[Message(role="user", content="hi")],
    )
    assert response.thinking == "Let me reason. Step 1: read. Step 2: reply."
    assert response.text == "The answer is 42."


@pytest.mark.asyncio
async def test_complete_captures_openai_reasoning_content(monkeypatch):
    """OpenAI-o1-shape reasoning_content chunks must accumulate too."""

    monkeypatch.setattr("litellm.acompletion", _fake_acompletion_openai_reasoning, raising=False)
    client = LLMClient()
    response = await client.complete(
        model="o1-mini",
        messages=[Message(role="user", content="hi")],
    )
    assert response.thinking == "Reasoning chunk one. Reasoning chunk two."
    assert response.text == "Final reply."


@pytest.mark.asyncio
async def test_streaming_emits_thinking_delta_events(monkeypatch):
    """The internal stream surfaces ``thinking_delta`` events so consumers
    other than ``complete()`` (e.g. a future delta-streaming UI) can
    distinguish reasoning chunks from final-text chunks."""

    monkeypatch.setattr("litellm.acompletion", _fake_acompletion_anthropic_thinking, raising=False)
    client = LLMClient()
    types = []
    async for ev in client.stream(
        model="claude-opus-4-7",
        messages=[Message(role="user", content="hi")],
    ):
        types.append(ev.type)
    # Exactly one thinking_delta per source chunk that had thinking.
    assert types.count("thinking_delta") == 3
    # The final-text chunk also produced a text_delta.
    assert types.count("text_delta") == 1
