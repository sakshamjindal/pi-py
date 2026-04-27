"""Thin LiteLLM wrapper.

The harness only needs three things from the provider layer: completion,
streaming completion, and a way to count tokens. LiteLLM's `acompletion`
gives us the first two with a single OpenAI-shaped surface across
providers; `token_counter` gives us the third.

The streaming path is canonical: `complete()` is sugar that consumes the
stream and returns a single `LLMResponse`. This keeps the production code
path identical between streaming and non-streaming callers.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from .types import LLMResponse, Message, StreamEvent, TokenUsage, ToolCall


class LLMError(Exception):
    """Raised when the underlying provider call fails. The original
    exception is attached as ``__cause__``."""


def _is_anthropic_model(model: str) -> bool:
    m = model.lower()
    return m.startswith(("claude", "anthropic/"))


def _messages_to_dicts(messages: list[Message] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if isinstance(m, Message):
            out.append(m.model_dump(exclude_none=True))
        else:
            out.append(dict(m))
    return out


def _apply_anthropic_caching(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None]:
    """Mark the system prompt and tool block as cacheable for Anthropic.

    LiteLLM passes through ``cache_control`` on system content blocks and
    on the trailing tool definition, so callers don't have to know which
    provider they are on.
    """

    new_messages: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") == "system" and isinstance(msg.get("content"), str):
            new_messages.append(
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "text",
                            "text": msg["content"],
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                }
            )
        else:
            new_messages.append(msg)

    new_tools = None
    if tools:
        new_tools = [dict(t) for t in tools]
        # Mark the last tool definition as a cache breakpoint.
        new_tools[-1] = {**new_tools[-1], "cache_control": {"type": "ephemeral"}}
    return new_messages, new_tools


class LLMClient:
    """Thin wrapper around LiteLLM.

    LiteLLM is imported lazily so the harness can be exercised in tests
    without the full provider dependency tree.
    """

    def __init__(self, *, default_temperature: float = 0.0):
        self.default_temperature = default_temperature

    async def complete(
        self,
        *,
        model: str,
        messages: list[Message] | list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Run a non-streaming completion by consuming the stream."""

        text_chunks: list[str] = []
        thinking_chunks: list[str] = []
        tool_calls_acc: dict[str, dict[str, Any]] = {}
        tool_call_order: list[str] = []
        usage = TokenUsage()
        finish_reason: str | None = None

        async for event in self.stream(
            model=model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            extra=extra,
        ):
            if event.type == "text_delta" and event.delta is not None:
                text_chunks.append(event.delta)
            elif event.type == "tool_call_start":
                tcid = event.tool_call_id or f"call_{len(tool_call_order)}"
                if tcid not in tool_calls_acc:
                    tool_calls_acc[tcid] = {
                        "id": tcid,
                        "name": event.tool_name or "",
                        "arguments_buffer": "",
                    }
                    tool_call_order.append(tcid)
                if event.tool_name:
                    tool_calls_acc[tcid]["name"] = event.tool_name
            elif event.type == "tool_call_delta":
                tcid = event.tool_call_id or (tool_call_order[-1] if tool_call_order else "")
                if tcid:
                    if tcid not in tool_calls_acc:
                        tool_calls_acc[tcid] = {
                            "id": tcid,
                            "name": event.tool_name or "",
                            "arguments_buffer": "",
                        }
                        tool_call_order.append(tcid)
                    if event.arguments_delta:
                        tool_calls_acc[tcid]["arguments_buffer"] += event.arguments_delta
                    if event.tool_name and not tool_calls_acc[tcid].get("name"):
                        tool_calls_acc[tcid]["name"] = event.tool_name
            elif event.type == "usage" and event.usage:
                usage = event.usage
            elif event.type == "message_stop":
                finish_reason = event.finish_reason

        tool_calls: list[ToolCall] = []
        for tcid in tool_call_order:
            entry = tool_calls_acc[tcid]
            buf = entry.get("arguments_buffer", "") or "{}"
            try:
                args = json.loads(buf) if buf.strip() else {}
            except json.JSONDecodeError:
                args = {"_raw": buf}
            tool_calls.append(
                ToolCall(id=entry["id"], name=entry.get("name") or "", arguments=args)
            )

        return LLMResponse(
            text="".join(text_chunks),
            thinking="".join(thinking_chunks),
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=finish_reason,
            raw={},
        )

    async def stream(
        self,
        *,
        model: str,
        messages: list[Message] | list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream provider events, normalised to ``StreamEvent``."""

        msg_dicts = _messages_to_dicts(messages)
        send_tools = tools

        if _is_anthropic_model(model):
            msg_dicts, send_tools = _apply_anthropic_caching(msg_dicts, tools)

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": msg_dicts,
            "temperature": temperature if temperature is not None else self.default_temperature,
            "stream": True,
            # OpenAI-compatible providers (OpenRouter, OpenAI itself, Azure,
            # etc.) only emit usage on the final chunk when this is set.
            # Without it, prompt_tokens / completion_tokens / total_tokens
            # all come back as 0, which silently breaks compaction's
            # threshold trigger and cost reporting.
            "stream_options": {"include_usage": True},
        }
        if send_tools:
            kwargs["tools"] = send_tools
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if extra:
            kwargs.update(extra)

        try:
            from litellm import acompletion
        except Exception as exc:  # pragma: no cover - import-time only
            raise LLMError("litellm is not available") from exc

        try:
            stream = await acompletion(**kwargs)
        except Exception as exc:
            raise LLMError(str(exc)) from exc

        # Track open tool calls keyed by index for OpenAI-style streaming.
        active_calls: dict[int, str] = {}
        finish_reason: str | None = None
        last_usage: TokenUsage | None = None

        try:
            async for chunk in stream:
                async for ev in _convert_chunk(chunk, active_calls):
                    if ev.type == "usage" and ev.usage is not None:
                        last_usage = ev.usage
                    if ev.type == "message_stop":
                        finish_reason = ev.finish_reason or finish_reason
                    yield ev
        except Exception as exc:
            raise LLMError(str(exc)) from exc

        if last_usage is None:
            yield StreamEvent(type="usage", usage=TokenUsage())
        yield StreamEvent(type="message_stop", finish_reason=finish_reason)


async def _convert_chunk(chunk: Any, active_calls: dict[int, str]) -> AsyncIterator[StreamEvent]:
    """Convert a single LiteLLM streaming chunk into ``StreamEvent``s."""

    # Usage often arrives on a dedicated chunk at the end.
    usage = getattr(chunk, "usage", None) or (
        chunk.get("usage") if isinstance(chunk, dict) else None
    )
    if usage:
        yield StreamEvent(type="usage", usage=_extract_usage(usage))

    choices = (
        getattr(chunk, "choices", None)
        or (chunk.get("choices") if isinstance(chunk, dict) else None)
        or []
    )
    for choice in choices:
        delta = getattr(choice, "delta", None) or (
            choice.get("delta") if isinstance(choice, dict) else None
        )
        finish = getattr(choice, "finish_reason", None) or (
            choice.get("finish_reason") if isinstance(choice, dict) else None
        )
        if delta is not None:
            content = getattr(delta, "content", None) or (
                delta.get("content") if isinstance(delta, dict) else None
            )
            if content:
                yield StreamEvent(type="text_delta", delta=content)
            tool_calls = getattr(delta, "tool_calls", None) or (
                delta.get("tool_calls") if isinstance(delta, dict) else None
            )
            if tool_calls:
                for tc in tool_calls:
                    idx = getattr(tc, "index", None)
                    if idx is None and isinstance(tc, dict):
                        idx = tc.get("index", 0)
                    idx = idx or 0
                    tcid = getattr(tc, "id", None) or (
                        tc.get("id") if isinstance(tc, dict) else None
                    )
                    fn = getattr(tc, "function", None) or (
                        tc.get("function") if isinstance(tc, dict) else None
                    )
                    name = None
                    args_delta = None
                    if fn is not None:
                        name = getattr(fn, "name", None) or (
                            fn.get("name") if isinstance(fn, dict) else None
                        )
                        args_delta = getattr(fn, "arguments", None) or (
                            fn.get("arguments") if isinstance(fn, dict) else None
                        )
                    if idx not in active_calls:
                        active_calls[idx] = tcid or f"call_{idx}"
                        yield StreamEvent(
                            type="tool_call_start",
                            tool_call_id=active_calls[idx],
                            tool_name=name,
                        )
                    if args_delta or name:
                        yield StreamEvent(
                            type="tool_call_delta",
                            tool_call_id=active_calls[idx],
                            tool_name=name,
                            arguments_delta=args_delta,
                        )
        if finish:
            yield StreamEvent(type="message_stop", finish_reason=finish)


def _extract_usage(raw: Any) -> TokenUsage:
    def _g(obj: Any, key: str, default: Any = 0) -> Any:
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    cached = 0
    pt_details = _g(raw, "prompt_tokens_details", None)
    if pt_details is not None:
        cached = _g(pt_details, "cached_tokens", 0) or 0

    return TokenUsage(
        prompt_tokens=int(_g(raw, "prompt_tokens", 0) or 0),
        completion_tokens=int(_g(raw, "completion_tokens", 0) or 0),
        total_tokens=int(_g(raw, "total_tokens", 0) or 0),
        cached_tokens=int(cached or 0),
        cost_usd=float(_g(raw, "cost", 0.0) or 0.0),
    )


def count_tokens(model: str, messages: list[Message] | list[dict[str, Any]]) -> int:
    """Best-effort token count via LiteLLM. Falls back to a char/4 heuristic
    when LiteLLM isn't importable (e.g. in lightweight tests)."""

    msg_dicts = _messages_to_dicts(messages)
    try:
        from litellm import token_counter

        return int(token_counter(model=model, messages=msg_dicts))
    except Exception:
        total = 0
        for m in msg_dicts:
            content = m.get("content")
            if isinstance(content, str):
                total += len(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        total += len(str(block.get("text", "")))
        return max(1, total // 4)
