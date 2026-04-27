"""Per-tool failure circuit breaker.

When the model gets stuck in a "guess another URL" or "try another
search variant" loop, individual tool failures are locally rational
but globally a death spiral. Each turn the model sees one 404 and
picks a plausible alternative; over many turns this burns context
without producing new information.

The breaker tracks consecutive *failures* of a named tool. After
``threshold`` failures in a row, the tool is **open** for
``cooldown_turns`` turns: the dispatcher refuses calls and returns
a synthetic message redirecting the model. A successful call
resets the counter; a non-failure call (e.g. a different tool) does
nothing.

Configured for ``web_fetch`` and ``web_search`` by default — both are
unbounded action spaces where consecutive failures are a strong
signal of strategy mismatch (the resource isn't where the model
thinks, no amount of retry will find it).

The breaker is **not** a global circuit on all tools. ``bash``,
``read``, ``edit``, ``write`` are explicitly excluded: a failing
bash could be a typo, a failing read a misspelled path. Strategy
mismatch isn't the dominant signal there.
"""

from __future__ import annotations

from dataclasses import dataclass

# Tools the breaker watches. Anything not in this set never trips a
# breaker, regardless of how often it fails.
WATCHED_TOOLS: frozenset[str] = frozenset({"web_fetch", "web_search"})


@dataclass
class CircuitOpen:
    """Returned when a tool's breaker is currently open."""

    tool_name: str
    turns_remaining: int
    failures_seen: int

    @property
    def synthetic_content(self) -> str:
        return (
            f"[circuit breaker] {self.failures_seen} consecutive `{self.tool_name}` "
            f"failures in this session — the URLs/queries you've been trying are "
            f"not working. {self.tool_name} is paused for {self.turns_remaining} "
            f"more turn(s) to force a strategy change. Try a different approach: "
            f"ask the user for the right URL/query, search locally with "
            f"`grep`/`glob`/`read`, or simplify your search terms. The breaker "
            f"resets on success or after the cooldown."
        )


@dataclass
class _ToolState:
    consecutive_failures: int = 0
    open_until_turn: int | None = None

    @property
    def is_open(self) -> bool:
        return self.open_until_turn is not None


class WebFetchCircuitBreaker:
    """Per-tool consecutive-failure tracker with cooldown."""

    def __init__(self, *, threshold: int = 3, cooldown_turns: int = 5) -> None:
        self._states: dict[str, _ToolState] = {}
        self._threshold = threshold
        self._cooldown = cooldown_turns
        self._turn = 0

    def advance_turn(self) -> None:
        """Bump the turn counter. The dispatcher calls this once per
        agent-loop turn so cooldowns can expire."""

        self._turn += 1

    def check(self, tool_name: str) -> CircuitOpen | None:
        """Return ``CircuitOpen`` if the tool is currently in cooldown,
        else ``None``. Untracked tools always return ``None``."""

        if tool_name not in WATCHED_TOOLS:
            return None
        state = self._states.get(tool_name)
        if state is None or state.open_until_turn is None:
            return None
        # Cooldown expired? Reset and let the call through.
        if self._turn >= state.open_until_turn:
            state.open_until_turn = None
            state.consecutive_failures = 0
            return None
        return CircuitOpen(
            tool_name=tool_name,
            turns_remaining=state.open_until_turn - self._turn,
            failures_seen=state.consecutive_failures,
        )

    def record_success(self, tool_name: str) -> None:
        """Successful call ⇒ reset the consecutive-failure counter."""

        if tool_name not in WATCHED_TOOLS:
            return
        state = self._states.get(tool_name)
        if state is not None:
            state.consecutive_failures = 0
            state.open_until_turn = None

    def record_failure(self, tool_name: str) -> None:
        """Failed call ⇒ increment counter, open breaker on threshold."""

        if tool_name not in WATCHED_TOOLS:
            return
        state = self._states.setdefault(tool_name, _ToolState())
        state.consecutive_failures += 1
        if state.consecutive_failures >= self._threshold and state.open_until_turn is None:
            state.open_until_turn = self._turn + self._cooldown
