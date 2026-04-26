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
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


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
