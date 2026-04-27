"""Replay the "spiral" session JSONL through ToolCallDedup + WebFetchCircuitBreaker.

The spiral session is the longest session on disk (~108 events) and
contains many ``web_fetch`` calls against github.com /
raw.githubusercontent.com URLs — several of which returned 404 (visible
in the result body as ``status: 4…``).  It also contains a duplicate
``web_fetch`` call with identical arguments.

This test loads the JSONL, walks its events through fresh guard
instances, and asserts:

* **dedup** would have caught the duplicate ``web_fetch`` call (same
  URL called on two different turns).
* **breaker** would have opened after consecutive 4xx-status
  ``web_fetch`` results (using ``threshold=2`` because the session has
  two back-to-back 404s before a success intervenes).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pyharness import ToolCallDedup, WebFetchCircuitBreaker

# ---------------------------------------------------------------------------
# Locate the spiral session JSONL
# ---------------------------------------------------------------------------

# Allow override via env var for CI / other machines.
_SPIRAL_SESSION_ENV = "SPIRAL_SESSION_JSONL"

# Default: the known path on the development machine.
_DEFAULT_SPIRAL = Path.home() / (
    ".pyharness/sessions/3b299fc52d3c904d/636852f3e07c498e9acb2263e135dc2f.jsonl"
)

SPIRAL_SESSION_PATH = Path(os.environ.get(_SPIRAL_SESSION_ENV, str(_DEFAULT_SPIRAL)))


def _load_events(path: Path) -> list[dict]:
    events: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def _is_4xx_body(result: str) -> bool:
    """Detect a 4xx HTTP status in the ``web_fetch`` result body.

    The original ``web_fetch`` tool returned ``ok=True`` even for 404s,
    so we inspect the body text for the ``status: 4`` prefix that the
    tool's metadata header emits.
    """
    for raw_line in result.split("\n")[:10]:
        stripped = raw_line.strip().lower()
        if stripped.startswith("status:"):
            code_part = stripped.split(":", 1)[1].strip()
            if code_part.startswith("4"):
                return True
    return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def spiral_events():
    if not SPIRAL_SESSION_PATH.is_file():
        pytest.skip(
            f"Spiral session JSONL not found at {SPIRAL_SESSION_PATH}; "
            f"set {_SPIRAL_SESSION_ENV} to override"
        )
    return _load_events(SPIRAL_SESSION_PATH)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestToolCallDedupReplay:
    """Walk the spiral session through ``ToolCallDedup`` and verify it
    catches the duplicate ``web_fetch`` call."""

    def test_dedup_catches_duplicate_web_fetch(self, spiral_events):
        """The session contains two ``web_fetch`` calls with identical
        arguments (same raw.githubusercontent.com URL for
        ``system-prompt.ts``).  The deduper must flag the second one."""

        dedup = ToolCallDedup(window=40)
        hits: list[tuple[int, str, dict]] = []  # (turn, tool_name, args)

        turn = 0
        # Build a map from call_id → (tool_name, arguments) for pairing
        # start → end.
        pending: dict[str, tuple[str, dict]] = {}

        for event in spiral_events:
            etype = event["type"]

            if etype == "assistant_message":
                turn += 1
                dedup.advance_turn()
                continue

            if etype == "tool_call_start":
                tool_name = event["tool_name"]
                arguments = event.get("arguments", {})
                call_id = event.get("call_id", "")
                pending[call_id] = (tool_name, arguments)

                hit = dedup.check(tool_name, arguments)
                if hit is not None:
                    hits.append((turn, tool_name, arguments))
                # We still record even if it was a hit, matching the
                # real loop's behaviour (record happens after execution).
                continue

            if etype == "tool_call_end":
                call_id = event.get("call_id", "")
                if call_id in pending:
                    tool_name, arguments = pending.pop(call_id)
                    dedup.record(tool_name, arguments)
                continue

        # There must be at least one dedup hit.
        assert len(hits) >= 1, f"Expected at least 1 dedup hit, got {len(hits)}"

        # The duplicate is a web_fetch to the system-prompt.ts URL.
        dup_tools = {h[1] for h in hits}
        assert "web_fetch" in dup_tools, (
            f"Expected a web_fetch duplicate; hit tools were: {dup_tools}"
        )

    def test_no_false_positives_on_unique_reads(self, spiral_events):
        """Unique ``read`` calls with different arguments must NOT be
        flagged as duplicates."""

        dedup = ToolCallDedup(window=40)
        read_hits: list[tuple[int, str, dict]] = []

        turn = 0
        pending: dict[str, tuple[str, dict]] = {}

        for event in spiral_events:
            etype = event["type"]

            if etype == "assistant_message":
                turn += 1
                dedup.advance_turn()
                continue

            if etype == "tool_call_start":
                tool_name = event["tool_name"]
                arguments = event.get("arguments", {})
                call_id = event.get("call_id", "")
                pending[call_id] = (tool_name, arguments)

                if tool_name == "read":
                    hit = dedup.check(tool_name, arguments)
                    if hit is not None:
                        read_hits.append((turn, tool_name, arguments))
                else:
                    dedup.check(tool_name, arguments)
                continue

            if etype == "tool_call_end":
                call_id = event.get("call_id", "")
                if call_id in pending:
                    tool_name, arguments = pending.pop(call_id)
                    dedup.record(tool_name, arguments)
                continue

        # All read calls in the spiral session have distinct arguments,
        # so there should be zero read dedup hits.
        assert len(read_hits) == 0, (
            f"Expected 0 read dedup hits (all unique args), got {len(read_hits)}: {read_hits}"
        )


class TestCircuitBreakerReplay:
    """Walk the spiral session through ``WebFetchCircuitBreaker`` and
    verify it would have opened on the consecutive 404 web_fetch calls.

    The session has two back-to-back 404 ``web_fetch`` results (turns 9
    and 10) before a 200 on turn 11 resets the counter.  With
    ``threshold=2`` the breaker opens after those two consecutive
    failures.
    """

    def test_breaker_opens_on_consecutive_4xx(self, spiral_events):
        breaker = WebFetchCircuitBreaker(threshold=2, cooldown_turns=5)
        opened = False
        open_events: list[tuple[int, str]] = []

        turn = 0
        pending: dict[str, tuple[str, dict]] = {}

        for event in spiral_events:
            etype = event["type"]

            if etype == "assistant_message":
                turn += 1
                breaker.advance_turn()
                continue

            if etype == "tool_call_start":
                tool_name = event["tool_name"]
                arguments = event.get("arguments", {})
                call_id = event.get("call_id", "")
                pending[call_id] = (tool_name, arguments)

                # Check if breaker is open before this call.
                state = breaker.check(tool_name)
                if state is not None:
                    opened = True
                    open_events.append((turn, tool_name))
                continue

            if etype == "tool_call_end":
                call_id = event.get("call_id", "")
                tool_name = event.get("tool_name", "")
                result = event.get("result", "")
                ok = event.get("ok", True)

                if call_id in pending:
                    pending.pop(call_id)

                # Determine failure: the original session marked
                # everything ok=True, but 4xx responses are failures
                # for the breaker's purposes.
                is_failure = (not ok) or _is_4xx_body(result)

                if is_failure:
                    breaker.record_failure(tool_name)
                else:
                    breaker.record_success(tool_name)
                continue

        assert opened, (
            "Expected the circuit breaker to open on consecutive 4xx "
            "web_fetch results, but it never did"
        )
        # The breaker should have opened for web_fetch specifically.
        open_tool_names = {name for _, name in open_events}
        assert "web_fetch" in open_tool_names

    def test_breaker_does_not_open_with_high_threshold(self, spiral_events):
        """With a high threshold (e.g. 10), the breaker should never
        open — the session doesn't have 10 consecutive 4xx failures."""

        breaker = WebFetchCircuitBreaker(threshold=10, cooldown_turns=5)
        opened = False

        turn = 0
        pending: dict[str, tuple[str, dict]] = {}

        for event in spiral_events:
            etype = event["type"]

            if etype == "assistant_message":
                turn += 1
                breaker.advance_turn()
                continue

            if etype == "tool_call_start":
                tool_name = event["tool_name"]
                call_id = event.get("call_id", "")
                pending[call_id] = (tool_name, event.get("arguments", {}))

                state = breaker.check(tool_name)
                if state is not None:
                    opened = True
                continue

            if etype == "tool_call_end":
                call_id = event.get("call_id", "")
                tool_name = event.get("tool_name", "")
                result = event.get("result", "")
                ok = event.get("ok", True)

                if call_id in pending:
                    pending.pop(call_id)

                is_failure = (not ok) or _is_4xx_body(result)
                if is_failure:
                    breaker.record_failure(tool_name)
                else:
                    breaker.record_success(tool_name)
                continue

        assert not opened, (
            "Breaker should NOT open with threshold=10 — the session "
            "has at most 2 consecutive 4xx failures"
        )


class TestSessionStructure:
    """Sanity-check the spiral session's shape."""

    def test_event_count(self, spiral_events):
        assert len(spiral_events) == 108

    def test_has_web_fetch_calls(self, spiral_events):
        wf = [
            e
            for e in spiral_events
            if e["type"] == "tool_call_start" and e["tool_name"] == "web_fetch"
        ]
        assert len(wf) >= 8, f"Expected ≥8 web_fetch calls, got {len(wf)}"

    def test_has_4xx_results(self, spiral_events):
        four_xx = [
            e
            for e in spiral_events
            if e["type"] == "tool_call_end"
            and e.get("tool_name") == "web_fetch"
            and _is_4xx_body(e.get("result", ""))
        ]
        assert len(four_xx) >= 2, f"Expected ≥2 4xx web_fetch results, got {len(four_xx)}"
