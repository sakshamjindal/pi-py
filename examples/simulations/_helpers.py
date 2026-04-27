"""Shared utilities for scenario simulations.

Three building blocks:

- ``ScriptedLLM`` / ``RaisingLLM`` — replays canned responses or raises.
  Used in mock-mode scenarios.
- ``install_scripted_llm`` / ``install_raising_llm`` — swap the
  ``complete``/``stream`` methods on a built ``CodingAgent``'s
  ``LLMClient``. The canonical pattern from
  ``coding-harness/tests/test_cli.py``: replacing ``agent.llm`` itself
  does NOT intercept (the inner ``Agent`` holds its own reference);
  replacing methods on the shared ``LLMClient`` instance does.
- ``make_project(tmp_path)`` — drops a ``.pyharness/`` marker so
  ``CodingAgent`` doesn't hit ``NoProjectError``.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from coding_harness import CodingAgent
from pyharness import LLMResponse


class ScriptedLLM:
    """Returns prepared responses in order; raises IndexError on overrun."""

    def __init__(self, responses: Iterable[LLMResponse]):
        self._responses: list[LLMResponse] = list(responses)
        self.calls: int = 0

    async def complete(self, **_: Any) -> LLMResponse:
        self.calls += 1
        if not self._responses:
            raise IndexError(
                f"ScriptedLLM exhausted after {self.calls} call(s); scenario expected fewer LLM calls."
            )
        return self._responses.pop(0)

    async def stream(self, **_: Any):
        if False:
            yield None


class RaisingLLM:
    """Raises the configured exception on every ``complete()`` call."""

    def __init__(self, exc: BaseException):
        self._exc = exc

    async def complete(self, **_: Any) -> LLMResponse:
        raise self._exc

    async def stream(self, **_: Any):
        if False:
            yield None


def make_project(tmp_path: Path) -> Path:
    """Create a ``.pyharness/`` marker in ``tmp_path`` and return it as
    the workspace. Equivalent to ``pyharness init``. Creates parents."""

    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".pyharness").mkdir(exist_ok=True)
    return tmp_path


def install_scripted_llm(agent: CodingAgent, responses: Iterable[LLMResponse]) -> ScriptedLLM:
    """Swap ``complete`` and ``stream`` on an already-built agent's
    ``LLMClient``. The shared instance is what the inner ``Agent``
    holds, so replacing its methods is what actually intercepts."""

    script = ScriptedLLM(responses)
    agent.llm.complete = script.complete  # type: ignore[assignment]
    agent.llm.stream = script.stream  # type: ignore[assignment]
    return script


def install_raising_llm(agent: CodingAgent, exc: BaseException) -> RaisingLLM:
    raiser = RaisingLLM(exc)
    agent.llm.complete = raiser.complete  # type: ignore[assignment]
    agent.llm.stream = raiser.stream  # type: ignore[assignment]
    return raiser
