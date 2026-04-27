"""Transparent context compaction.

When the token count exceeds a threshold, the compactor keeps the system
prompt and the last N messages verbatim and summarises everything in
between via a separate (typically cheaper) model call. The summary is
injected as a synthetic user message so the agent treats it as
authoritative context.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .llm import LLMClient, count_tokens
from .types import Message


@dataclass
class CompactionResult:
    messages: list[Message]
    tokens_before: int
    tokens_after: int
    compacted: bool
    summary: str = ""


class Compactor:
    def __init__(
        self,
        llm: LLMClient,
        *,
        summarization_model: str,
        keep_recent_count: int = 20,
        target_token_reduction: float = 0.5,
        max_summary_tokens: int = 8_000,
    ):
        self.llm = llm
        self.summarization_model = summarization_model
        self.keep_recent_count = keep_recent_count
        self.target_token_reduction = target_token_reduction
        # Caps the summarisation LLM call's output budget. Without this we
        # fall through to the model's default (64k for Haiku/Opus 4.x),
        # which OpenRouter charges credits-on-hand against — making a
        # compaction call require ~$5 of pre-paid balance even though
        # actual summaries are typically <2k tokens. 8k is a generous
        # ceiling that covers any reasonable summary of a 130k+ transcript.
        self.max_summary_tokens = max_summary_tokens

    async def maybe_compact(
        self,
        messages: list[Message],
        threshold_tokens: int,
        *,
        model_for_count: str,
    ) -> CompactionResult:
        before = count_tokens(model_for_count, messages)
        if before <= threshold_tokens or len(messages) <= self.keep_recent_count + 1:
            return CompactionResult(
                messages=messages, tokens_before=before, tokens_after=before, compacted=False
            )

        # Find the leading system message (if any) to keep verbatim.
        head: list[Message] = []
        body_start = 0
        if messages and messages[0].role == "system":
            head = [messages[0]]
            body_start = 1

        tail_count = max(1, self.keep_recent_count)
        tail = (
            messages[-tail_count:]
            if tail_count < len(messages) - body_start
            else messages[body_start:]
        )
        middle = messages[body_start:-tail_count] if tail_count < len(messages) - body_start else []

        if not middle:
            return CompactionResult(
                messages=messages, tokens_before=before, tokens_after=before, compacted=False
            )

        summary = await self._summarise(middle)
        synthetic = Message(
            role="user",
            content=(
                "[The earlier portion of this conversation has been compacted.\n"
                "Summary of what happened up to this point:]\n"
                f"{summary}"
            ),
        )
        new_messages = [*head, synthetic, *list(tail)]
        after = count_tokens(model_for_count, new_messages)
        return CompactionResult(
            messages=new_messages,
            tokens_before=before,
            tokens_after=after,
            compacted=True,
            summary=summary,
        )

    async def _summarise(self, middle: list[Message]) -> str:
        # Build a compact prompt for the summariser. We render messages as
        # plain text so the summary model doesn't try to call tools.
        rendered = []
        for m in middle:
            role = m.role
            if isinstance(m.content, str):
                body = m.content
            else:
                try:
                    body = json.dumps(m.content)
                except TypeError:
                    body = str(m.content)
            if m.tool_calls:
                body += "\n[tool_calls=" + json.dumps(m.tool_calls) + "]"
            rendered.append(f"{role}: {body}")

        prompt = (
            "You are summarising an in-progress agent session so that the "
            "agent can continue with full task context but a smaller "
            "transcript. Preserve: the user's original goals, key decisions, "
            "tool results that affect future steps, file paths touched, and "
            "any open questions. Drop verbose tool output that no longer "
            "matters. Output a single compact summary, no preamble.\n\n"
            "----- transcript -----\n" + "\n\n".join(rendered)
        )
        msgs = [Message(role="user", content=prompt)]
        resp = await self.llm.complete(
            model=self.summarization_model,
            messages=msgs,
            max_tokens=self.max_summary_tokens,
        )
        return resp.text.strip() or "(no summary returned)"
