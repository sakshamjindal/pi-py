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
import os
import sys
from collections.abc import AsyncIterator
from typing import Any

from .types import LLMResponse, Message, StreamEvent, TokenUsage, ToolCall


class LLMError(Exception):
    """Raised when the underlying provider call fails. The original
    exception is attached as ``__cause__``."""


def _is_anthropic_model(model: str) -> bool:
    m = model.lower()
    return m.startswith(("claude", "anthropic/"))


# Provider prefix → env var LiteLLM expects. Only listed providers are
# checked; anything else (custom endpoints, local models, future providers)
# is left to LiteLLM to validate at call time.
_PROVIDER_API_KEY_VARS: dict[str, str] = {
    "openrouter/": "OPENROUTER_API_KEY",
    "anthropic/": "ANTHROPIC_API_KEY",
    "openai/": "OPENAI_API_KEY",
    "gemini/": "GEMINI_API_KEY",
    "groq/": "GROQ_API_KEY",
    "mistral/": "MISTRAL_API_KEY",
    "deepseek/": "DEEPSEEK_API_KEY",
}


def _check_api_key_for_model(model: str) -> None:
    """Fail fast with an actionable message when the env var LiteLLM needs
    is missing. Without this we get an opaque 401 from the provider with
    no hint about which env var is wrong (or, worse, LiteLLM picks up the
    *other* provider's key and authenticates against the wrong endpoint).
    """

    m = model.lower()
    # Bare ``claude-...`` IDs route to Anthropic when no provider prefix is set.
    if m.startswith("claude") and "/" not in m:
        env_var = "ANTHROPIC_API_KEY"
    else:
        env_var = next(
            (v for prefix, v in _PROVIDER_API_KEY_VARS.items() if m.startswith(prefix)),
            "",
        )
    if env_var and not os.environ.get(env_var):
        raise LLMError(
            f"{env_var} is not set. Model {model!r} requires it. "
            f"Export it in your shell (e.g. `export {env_var}=...`) and retry."
        )


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
            elif event.type == "thinking_delta" and event.delta is not None:
                thinking_chunks.append(event.delta)
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

        # Finalise cost. Streaming chunks don't carry a populated ``cost``
        # field, so ``_extract_usage`` left it at 0.0; resolve it now from
        # the model id and accumulated token counts.
        if usage.cost_usd == 0.0 and usage.total_tokens > 0:
            usage = usage.model_copy(update={"cost_usd": _resolve_cost(model, usage)})

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

        _check_api_key_for_model(model)

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
            # Provider thinking / extended-reasoning chunks. Anthropic
            # returns ``delta.thinking`` when extended thinking is on;
            # OpenAI's o1-class models return ``delta.reasoning_content``.
            # Without these branches, the reasoning text is silently
            # dropped — paid for in tokens, lost on the floor.
            thinking = getattr(delta, "thinking", None) or (
                delta.get("thinking") if isinstance(delta, dict) else None
            )
            if not thinking:
                thinking = getattr(delta, "reasoning_content", None) or (
                    delta.get("reasoning_content") if isinstance(delta, dict) else None
                )
            if thinking:
                yield StreamEvent(type="thinking_delta", delta=thinking)
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
        # Streaming chunks don't carry a populated ``cost`` field; the
        # caller resolves cost via ``_resolve_cost`` once the stream
        # finishes and the model id is in scope.
        cost_usd=float(_g(raw, "cost", 0.0) or 0.0),
    )


def _pricing_lookup_id(model: str) -> str:
    """Map our model id to the form LiteLLM's pricing table is keyed on.

    Two transformations:

    1. **Strip routing prefixes.** LiteLLM uses ``openrouter/anthropic/...``
       for routing but keys its pricing table on the underlying provider
       model id (``claude-...`` for Anthropic). We strip a single known
       routing prefix and, for OpenRouter's three-segment ids, drop the
       vendor segment too.
    2. **Normalise version separators.** OpenRouter exposes Anthropic
       models with dot-separated versions (``claude-haiku-4.5``); the
       LiteLLM pricing table uses dashes (``claude-haiku-4-5``). They
       refer to the same model. We rewrite Anthropic-shaped ids so the
       lookup succeeds.
    """

    m = model.lower()
    base = model
    for prefix in ("openrouter/", "openai/", "azure/"):
        if m.startswith(prefix):
            stripped = model[len(prefix) :]
            # OpenRouter wraps the model id as ``openrouter/<vendor>/<model>``;
            # the underlying pricing key is just ``<model>`` for Anthropic
            # ids and ``<vendor>/<model>`` is fine for others. Try the
            # bare model first (Anthropic convention).
            base = stripped.split("/", 1)[1] if "/" in stripped else stripped
            break

    # Anthropic ids in the LiteLLM pricing table use dashes between
    # version segments; OpenRouter exposes them with dots. Rewrite.
    if base.lower().startswith("claude") or base.lower().startswith("anthropic/"):
        base = base.replace(".", "-")
    return base


def _resolve_cost(model: str, usage: TokenUsage) -> float:
    """Compute USD cost from token counts using LiteLLM's pricing table.

    LiteLLM doesn't populate the streaming chunk's ``cost`` field, so
    ``_extract_usage`` returns 0. We finalise it here once the stream
    finishes and we know which model was used.

    Returns 0.0 on any failure (unknown model, import failure, malformed
    pricing entry) and emits a one-line stderr note so silent pricing
    misses become visible. We don't raise — cost telemetry is best-effort.
    """

    if usage.total_tokens <= 0:
        return 0.0
    try:
        from litellm import cost_per_token

        prompt_cost, completion_cost = cost_per_token(
            model=_pricing_lookup_id(model),
            prompt_tokens=max(usage.prompt_tokens - usage.cached_tokens, 0),
            completion_tokens=usage.completion_tokens,
        )
        cost = float(prompt_cost) + float(completion_cost)
    except Exception as exc:
        sys.stderr.write(f"[llm] cost lookup failed for {model!r}: {type(exc).__name__}: {exc}\n")
        return 0.0
    if cost <= 0.0 and usage.total_tokens > 0:
        sys.stderr.write(
            f"[llm] no pricing entry for {model!r}; recorded cost as 0.0 "
            f"despite {usage.total_tokens} tokens. "
            f"If this is wrong, file an issue or pin a different LiteLLM version.\n"
        )
    return cost


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
