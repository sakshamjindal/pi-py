"""Per-session tool-call deduplication for read-only tools.

Some tools have no value when re-called with identical arguments within
a single session — re-reading a file the model already read this turn
doesn't produce new bytes; re-fetching a URL that just 404'd doesn't
change the response. The model issues these duplicates because each
turn it reasons over local state without remembering it already
called the same thing earlier.

The deduper keeps a bounded LRU per tool name keyed on a stable hash
of the call arguments. On a hit, the dispatcher skips execution and
returns a synthetic message that nudges the model to make progress
without re-running.

Only **read-only** tools get deduped (``read``, ``web_fetch``,
``web_search``, ``grep``, ``glob``). Mutating tools (``bash``,
``edit``, ``write``) bypass — they have side effects and the model
might legitimately want to write the same content again after a
prior failure.

Construct one ``ToolCallDedup`` per ``Agent``. Tests pin which tools
participate and the bypass list.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

# Tools that ARE deduped. Adding a new tool to this set means: re-calls
# with identical args within ``window`` turns return a synthetic
# "already called" result instead of executing. Anything not in the set
# always executes.
DEDUPED_TOOLS: frozenset[str] = frozenset(
    {
        "read",
        "web_fetch",
        "web_search",
        "grep",
        "glob",
    }
)


@dataclass
class DedupHit:
    """Returned by ``check`` when the (tool, args) pair was seen
    recently. The dispatcher uses ``synthetic_content`` as the tool
    result and skips execution."""

    tool_name: str
    turns_ago: int

    @property
    def synthetic_content(self) -> str:
        return (
            f"[duplicate call] You called `{self.tool_name}` with these exact "
            f"arguments {self.turns_ago} turn(s) ago in this session. The "
            f"result is in your transcript above. Re-running is a no-op — "
            f"make progress without re-calling. If you genuinely need fresh "
            f"results, change the arguments (e.g. different path/URL/query) "
            f"or take a different action."
        )


class ToolCallDedup:
    """LRU-bounded per-session deduper for read-only tool calls."""

    def __init__(self, *, window: int = 20) -> None:
        # OrderedDict acts as the LRU. Keys are ``(tool_name, args_hash)``.
        # Values are the turn number at which we recorded the call.
        self._seen: OrderedDict[tuple[str, str], int] = OrderedDict()
        self._window = window
        self._turn = 0

    def advance_turn(self) -> None:
        """Bump the turn counter. Called once per loop turn so the
        ``turns_ago`` arithmetic in ``DedupHit`` is meaningful."""

        self._turn += 1

    def check(self, tool_name: str, arguments: dict[str, Any]) -> DedupHit | None:
        """Return ``DedupHit`` if this exact call is in the recent
        window, else ``None``. Tools not in ``DEDUPED_TOOLS`` always
        return ``None`` (they bypass dedup entirely)."""

        if tool_name not in DEDUPED_TOOLS:
            return None
        key = (tool_name, _stable_hash(arguments))
        if key not in self._seen:
            return None
        previous_turn = self._seen[key]
        # Move-to-end so it stays "fresh" in the LRU.
        self._seen.move_to_end(key)
        return DedupHit(tool_name=tool_name, turns_ago=max(self._turn - previous_turn, 1))

    def record(self, tool_name: str, arguments: dict[str, Any]) -> None:
        """Record that this call was just made. Bypassed tools are
        still recorded so future invocations of *other* deduped tools
        with the same key (which can't happen given the tuple includes
        the name, but kept for symmetry) work consistently."""

        if tool_name not in DEDUPED_TOOLS:
            return
        key = (tool_name, _stable_hash(arguments))
        self._seen[key] = self._turn
        self._seen.move_to_end(key)
        while len(self._seen) > self._window:
            self._seen.popitem(last=False)


def _stable_hash(arguments: dict[str, Any]) -> str:
    """Hash that's order-independent for dicts. Two argument dicts
    that differ only in key order must collide so e.g.
    ``{"a": 1, "b": 2}`` and ``{"b": 2, "a": 1}`` count as the same call.

    Falls back to ``str`` for objects JSON can't serialise — best-effort,
    we'd rather over-dedupe than miss a hit on a malformed arg.
    """

    try:
        canonical = json.dumps(arguments, sort_keys=True, default=str)
    except (TypeError, ValueError):
        canonical = str(arguments)
    return hashlib.sha1(canonical.encode("utf-8"), usedforsecurity=False).hexdigest()
