"""Token-cost logger extension.

Subscribes to ``after_llm_call``, accumulates token usage and dollar cost,
and writes a JSONL record per call to a file in the workspace's
``.pyharness/`` directory.

Usage: drop this file into ``~/.pyharness/extensions/`` or
``<project>/.pyharness/extensions/``. The harness loads it automatically
unless ``--bare`` is passed.
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
