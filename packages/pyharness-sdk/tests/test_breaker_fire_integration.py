"""Integration test: circuit breaker fires after N consecutive tool failures.

Registers a ``_FailingFetch`` tool that always raises ``ToolError``,
runs an Agent against a real LLM, and asserts the 4th fetch attempt
gets the breaker's synthetic "circuit breaker" message instead of
executing.

Skipped when ``OPENROUTER_API_KEY`` is unset.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from pyharness import (
    Agent,
    AgentOptions,
    EventBus,
    LLMClient,
    Session,
    Tool,
    ToolContext,
    ToolError,
    ToolRegistry,
    ToolResult,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("OPENROUTER_API_KEY"),
        reason="OPENROUTER_API_KEY not set; integration suite skipped",
    ),
]

MODEL = "openrouter/anthropic/claude-haiku-4-5"


# ---------------------------------------------------------------------------
# Failing web_fetch replacement
# ---------------------------------------------------------------------------


class _FetchArgs(BaseModel):
    url: str = Field(description="URL to fetch.")
    timeout: int = Field(default=30, description="Timeout in seconds.")


class _FailingFetch(Tool):
    """A ``web_fetch`` stand-in that always fails with a ToolError."""

    name = "web_fetch"
    description = (
        "Fetch a URL via HTTPS. This tool is currently broken and will "
        "always fail — used for testing the circuit breaker."
    )
    args_schema = _FetchArgs

    async def execute(self, args: _FetchArgs, ctx: ToolContext):  # type: ignore[override]
        raise ToolError(f"simulated network failure fetching {args.url}")


class _DoneArgs(BaseModel):
    reason: str = Field(description="Short reason the task is complete.")


class _DoneTool(Tool):
    name = "done"
    description = (
        "Call this when the task is complete or when you cannot proceed. "
        "Returns terminate=True so the agent stops."
    )
    args_schema = _DoneArgs

    async def execute(self, args: _DoneArgs, ctx: ToolContext):  # type: ignore[override]
        return ToolResult(content=f"acknowledged: {args.reason}", terminate=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_agent(tmp_path: Path) -> Agent:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)

    registry = ToolRegistry()
    registry.register(_FailingFetch())
    registry.register(_DoneTool())

    session = Session.new(workspace, base_dir=tmp_path / "sessions")

    return Agent(
        AgentOptions(
            model=MODEL,
            max_turns=12,
            tool_execution="sequential",
            # Dedup OFF so every fetch attempt actually reaches the breaker.
            tool_dedup_enabled=False,
            # Breaker trips after 3 consecutive failures.
            web_fetch_failure_threshold=3,
            web_fetch_cooldown_turns=5,
        ),
        system_prompt=(
            "You are a helpful agent. You have a web_fetch tool and a done tool. "
            "When asked to fetch URLs, call web_fetch for each URL in a separate "
            "tool call. If a tool is paused or you cannot proceed, call done."
        ),
        tool_registry=registry,
        session=session,
        event_bus=EventBus(),
        workspace=workspace,
        llm=LLMClient(),
    )


def _read_session_events(session: Session) -> list[dict]:
    events: list[dict] = []
    with session.log_path.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

PROMPT = (
    "Fetch https://example.com/a, then https://example.com/b, then "
    "https://example.com/c, then https://example.com/d in separate "
    "web_fetch calls. Make each call one at a time. After all fetches "
    "are done (or if you cannot proceed), call the done tool."
)


@pytest.mark.asyncio
async def test_circuit_breaker_fires_on_4th_fetch(tmp_path):
    """After 3 consecutive ``web_fetch`` failures the breaker opens and
    the 4th attempt gets a synthetic "circuit breaker" message."""

    agent = _build_agent(tmp_path)
    await agent.run(PROMPT)

    events = _read_session_events(agent.session)

    # Collect all tool_call_end events for web_fetch.
    wf_ends = [
        e for e in events if e["type"] == "tool_call_end" and e.get("tool_name") == "web_fetch"
    ]

    # We expect at least 4 web_fetch tool_call_end events (3 real
    # failures + 1 breaker-intercepted).
    assert len(wf_ends) >= 4, (
        f"Expected ≥4 web_fetch tool_call_end events, got {len(wf_ends)}: "
        f"{[e.get('result', '')[:80] for e in wf_ends]}"
    )

    # At least one of the results must contain the circuit breaker
    # synthetic message.
    breaker_results = [e for e in wf_ends if "circuit breaker" in e.get("result", "").lower()]
    assert len(breaker_results) >= 1, (
        "Expected at least one web_fetch result containing 'circuit breaker', "
        f"but none found. Results: {[e.get('result', '')[:120] for e in wf_ends]}"
    )

    # The breaker result should be the 4th or later (first 3 are real failures).
    breaker_indices = [
        i for i, e in enumerate(wf_ends) if "circuit breaker" in e.get("result", "").lower()
    ]
    assert all(idx >= 3 for idx in breaker_indices), (
        f"Breaker fired too early — expected index ≥3, got {breaker_indices}"
    )
