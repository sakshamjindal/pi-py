"""LLM client tests using a fake LiteLLM acompletion."""

from __future__ import annotations

import json
from typing import Any

import pytest

from pyharness.llm import LLMClient, LLMError
from pyharness.types import Message


@pytest.fixture(autouse=True)
def _stub_provider_keys(monkeypatch):
    """Most tests stub ``litellm.acompletion`` so they never actually
    need a real key — but the LLM client now fails fast when the env
    var for the chosen model is missing. Set placeholder keys so those
    tests skip the guard. Tests that *want* to exercise the guard use
    ``monkeypatch.delenv`` explicitly."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")


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


@pytest.mark.asyncio
async def test_missing_provider_api_key_fails_fast(monkeypatch):
    """When the env var LiteLLM expects for a model is unset, the client
    must raise an ``LLMError`` *before* the network call so the user gets
    an actionable message instead of an opaque provider 401."""

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    client = LLMClient()
    with pytest.raises(LLMError, match="OPENROUTER_API_KEY is not set"):
        await client.complete(
            model="openrouter/anthropic/claude-haiku-4-5",
            messages=[Message(role="user", content="hi")],
        )


@pytest.mark.asyncio
async def test_missing_anthropic_key_fails_fast_for_bare_claude(monkeypatch):
    """A bare ``claude-...`` model id (no provider prefix) routes to
    Anthropic, so it should require ANTHROPIC_API_KEY."""

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = LLMClient()
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY is not set"):
        await client.complete(
            model="claude-opus-4-7",
            messages=[Message(role="user", content="hi")],
        )


@pytest.mark.asyncio
async def test_unknown_provider_does_not_block(monkeypatch):
    """Models without a recognised prefix must not be blocked by the
    fast-fail guard — LiteLLM might still know how to route them."""

    # No env vars set; this would normally fail at the litellm call, but
    # we stub acompletion to confirm the guard didn't raise first.
    monkeypatch.setattr("litellm.acompletion", _fake_acompletion_text, raising=False)
    client = LLMClient()
    response = await client.complete(
        model="some-custom-model",
        messages=[Message(role="user", content="hi")],
    )
    assert response.text  # made it past the guard


# ---------------------------------------------------------------------------
# Cost telemetry
# ---------------------------------------------------------------------------


def test_resolve_cost_returns_nonzero_for_known_model():
    """LiteLLM ships a pricing table; a known Anthropic model must
    resolve to a non-zero cost when given non-zero tokens. This is the
    regression test for the silent-zero bug fixed in this commit."""

    from pyharness.llm import _resolve_cost
    from pyharness.types import TokenUsage

    cost = _resolve_cost(
        "claude-haiku-4-5",
        TokenUsage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500),
    )
    assert cost > 0, "expected a non-zero cost for a known-priced model"


def test_resolve_cost_strips_provider_prefix():
    """OpenRouter-prefixed Anthropic models must resolve to the same
    cost as the underlying Anthropic model. We strip the routing
    prefix before LiteLLM's pricing lookup — LiteLLM strips prefixes
    for *routing* but not for cost lookup."""

    from pyharness.llm import _resolve_cost
    from pyharness.types import TokenUsage

    usage = TokenUsage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500)
    direct = _resolve_cost("claude-haiku-4-5", usage)
    via_or = _resolve_cost("openrouter/anthropic/claude-haiku-4-5", usage)
    assert direct > 0
    assert via_or > 0
    # Same underlying model ⇒ same price.
    assert abs(direct - via_or) < 1e-9


def test_resolve_cost_normalises_dot_versions_for_anthropic():
    """OpenRouter exposes Anthropic models with dot-separated versions
    (``claude-haiku-4.5``); LiteLLM's pricing table uses dashes
    (``claude-haiku-4-5``). They refer to the same model. The resolver
    must map between them so callers using OpenRouter's natural id
    form actually get non-zero cost — this is the regression test for
    the silent-zero we hit in real CLI runs."""

    from pyharness.llm import _resolve_cost
    from pyharness.types import TokenUsage

    usage = TokenUsage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500)
    cost = _resolve_cost("openrouter/anthropic/claude-haiku-4.5", usage)
    assert cost > 0, (
        "OpenRouter-style dot version (`claude-haiku-4.5`) must resolve "
        "to the same price as the dash form. Got 0.0 — pricing lookup is broken."
    )


def test_resolve_cost_zero_when_no_tokens():
    """No tokens ⇒ no cost. Don't hit the pricing table for an empty call."""

    from pyharness.llm import _resolve_cost
    from pyharness.types import TokenUsage

    assert _resolve_cost("claude-haiku-4-5", TokenUsage()) == 0.0


def test_resolve_cost_excludes_cached_tokens(monkeypatch):
    """Cached tokens are charged at a discount by the provider, but the
    important invariant for our resolver is: we hand LiteLLM the
    non-cached prompt-token count so we don't double-charge for a
    cached prefix. This test pins that wire-up by stubbing
    cost_per_token and checking what we passed."""

    captured: dict = {}

    def fake_cost_per_token(*, model, prompt_tokens, completion_tokens, **_):
        captured["model"] = model
        captured["prompt_tokens"] = prompt_tokens
        captured["completion_tokens"] = completion_tokens
        return (0.001, 0.002)

    monkeypatch.setattr("litellm.cost_per_token", fake_cost_per_token, raising=False)

    from pyharness.llm import _resolve_cost
    from pyharness.types import TokenUsage

    _resolve_cost(
        "claude-haiku-4-5",
        TokenUsage(
            prompt_tokens=1000,
            completion_tokens=200,
            total_tokens=1200,
            cached_tokens=300,
        ),
    )
    assert captured["prompt_tokens"] == 700, (
        "non-cached prompt tokens should be billed; cached prefix is separate"
    )
    assert captured["completion_tokens"] == 200


@pytest.mark.asyncio
async def test_complete_populates_cost_usd(monkeypatch):
    """End-to-end: the LLMResponse returned from ``complete()`` must
    have a non-zero ``cost_usd`` for a known-priced model. Before the
    fix, this was always 0.0 because we read a field LiteLLM doesn't
    populate on streaming chunks."""

    monkeypatch.setattr("litellm.acompletion", _fake_acompletion_text, raising=False)
    client = LLMClient()
    response = await client.complete(
        model="claude-haiku-4-5",
        messages=[Message(role="user", content="hi")],
    )
    assert response.usage.total_tokens > 0
    assert response.usage.cost_usd > 0, (
        "complete() must finalise cost from token counts; "
        f"got total_tokens={response.usage.total_tokens} cost={response.usage.cost_usd}"
    )


@pytest.mark.asyncio
async def test_complete_warns_on_unknown_model(monkeypatch, capsys):
    """A model with no pricing entry should produce a stderr note so
    silent zeros become visible, but must not raise."""

    monkeypatch.setattr("litellm.acompletion", _fake_acompletion_text, raising=False)
    client = LLMClient()
    response = await client.complete(
        model="some-totally-unknown-model-that-litellm-has-never-heard-of",
        messages=[Message(role="user", content="hi")],
    )
    assert response.usage.cost_usd == 0.0
    captured = capsys.readouterr()
    assert "[llm]" in captured.err
