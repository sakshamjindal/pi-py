"""Audit-logger extension.

Records every tool start and tool end as a structured JSON line. Useful
for compliance, post-hoc review, and replay verification.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from pyharness import ExtensionAPI, HookOutcome


_AUDIT_FILE_NAME = "audit.jsonl"


def register(api: ExtensionAPI) -> None:
    api.on("before_tool_call", _log_start)
    api.on("after_tool_call", _log_end)


def _audit_path(ctx) -> Path:
    return Path(ctx.workspace) / ".pyharness" / _AUDIT_FILE_NAME


def _write(path: Path, record: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except OSError:
        pass


async def _log_start(event, ctx):
    _write(
        _audit_path(ctx),
        {
            "timestamp": time.time(),
            "session_id": ctx.session_id,
            "event": "tool_call_start",
            "tool": event.payload.get("tool_name"),
            "arguments": event.payload.get("arguments"),
        },
    )
    return HookOutcome.cont()


async def _log_end(event, ctx):
    _write(
        _audit_path(ctx),
        {
            "timestamp": time.time(),
            "session_id": ctx.session_id,
            "event": "tool_call_end",
            "tool": event.payload.get("tool_name"),
            "ok": event.payload.get("ok"),
            "duration_ms": event.payload.get("duration_ms"),
        },
    )
    return HookOutcome.cont()
