"""Token-cost logger extension.

Subscribes to ``after_llm_call``, captures per-call token usage and
dollar cost from the LLM response, and writes a JSONL record per call
to ``<workspace>/.pyharness/cost.jsonl``.

This is the canonical observability pattern in pyharness: the kernel
session log records *what happened* (messages, tool calls, sequence
numbers); cross-cutting concerns like cost / latency / audit trails
ride on the event bus and persist via extensions like this one. The
session log stays minimal; observability is opt-in.

Usage:

  1. Drop this file into ``~/.pyharness/extensions/`` or
     ``<project>/.pyharness/extensions/``.
  2. Enable it via the named agent's frontmatter:
         extensions:
           - cost_logger
     ...or programmatically via ``CodingAgentConfig.extensions_enabled=["cost_logger"]``.
     Extensions are NEVER auto-loaded — see DESIGN.md principle 7.

Then ``cat .pyharness/cost.jsonl`` to see one record per LLM call:
``{"timestamp", "session_id", "prompt_tokens", "completion_tokens",
"cached_tokens", "cost_usd"}``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from pyharness import ExtensionAPI, HookOutcome

_COST_FILE_NAME = "cost.jsonl"


def register(api: ExtensionAPI) -> None:
    api.on("after_llm_call", _log_cost)


async def _log_cost(event, ctx):
    response = event.payload.get("response") or {}
    usage = response.get("usage") or {}

    record = {
        "timestamp": time.time(),
        "session_id": ctx.session_id,
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "cached_tokens": usage.get("cached_tokens", 0),
        "cost_usd": usage.get("cost_usd", 0.0),
    }

    log_dir = Path(ctx.workspace) / ".pyharness"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / _COST_FILE_NAME).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError:
        pass

    return HookOutcome.cont()
