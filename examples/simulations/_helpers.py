"""Shared utilities for scenario simulations.

Three building blocks:

- ``ScriptedLLM`` — replays a list of canned ``LLMResponse`` objects in
  order, ignoring inputs. Mirrors the ``_ScriptedLLM`` helper in
  ``pyharness-sdk/tests/test_loop.py``; promoted here so scenario
  modules don't have to re-define it.
- ``make_project(tmp_path)`` — drops a ``.pyharness/`` marker so
  ``CodingAgent`` doesn't hit ``NoProjectError``.
- ``mock_llm_in_agent(monkeypatch, responses)`` — patches
  ``CodingAgent.__init__`` to inject a ``ScriptedLLM`` after the real
  init runs, matching the pattern in
  ``coding-harness/tests/test_cli.py`` and
  ``tui/tests/test_tui_smoke.py``.

Anything more elaborate belongs in the scenario file that needs it.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from coding_harness import CodingAgent
from pyharness import LLMResponse


class ScriptedLLM:
    """Returns the prepared responses in order, ignoring inputs.

    Once the script is exhausted, ``complete()`` raises ``IndexError``
    so the test fails loudly instead of silently looping. Streaming is
    a no-op generator unless overridden.
    """

    def __init__(self, responses: Iterable[LLMResponse]):
        self._responses: list[LLMResponse] = list(responses)
        self.calls: int = 0

    async def complete(self, **_: Any) -> LLMResponse:
        self.calls += 1
        if not self._responses:
            raise IndexError(
                f"ScriptedLLM exhausted: scenario expected fewer LLM calls (received {self.calls})."
            )
        return self._responses.pop(0)

    async def stream(self, **_: Any):
        if False:
            yield None


class RaisingLLM:
    """Raises the configured exception on every ``complete()`` call.

    Used to verify the loop's LLM-error termination path.
    """

    def __init__(self, exc: BaseException):
        self._exc = exc

    async def complete(self, **_: Any) -> LLMResponse:
        raise self._exc

    async def stream(self, **_: Any):
        if False:
            yield None


def make_project(tmp_path: Path) -> Path:
    """Create a ``.pyharness/`` marker in ``tmp_path`` and return it as
    the workspace. Equivalent to running ``pyharness init``. Creates any
    missing parent directories so nested workspaces work."""

    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".pyharness").mkdir(exist_ok=True)
    return tmp_path


def install_scripted_llm(agent: CodingAgent, responses: Iterable[LLMResponse]) -> ScriptedLLM:
    """Swap the ``complete`` and ``stream`` methods on an already-built
    agent's LLMClient. This is the canonical pattern from
    ``coding-harness/tests/test_cli.py`` — the inner ``Agent`` holds a
    reference to ``CodingAgent.llm`` (the LLMClient instance), and
    replacing methods on that shared instance is what actually intercepts
    LLM calls in the loop. Replacing ``agent.llm`` itself does NOT work."""

    script = ScriptedLLM(responses)
    agent.llm.complete = script.complete  # type: ignore[assignment]
    agent.llm.stream = script.stream  # type: ignore[assignment]
    return script


def install_raising_llm(agent: CodingAgent, exc: BaseException) -> RaisingLLM:
    """Same as ``install_scripted_llm`` but every call raises."""

    raiser = RaisingLLM(exc)
    agent.llm.complete = raiser.complete  # type: ignore[assignment]
    agent.llm.stream = raiser.stream  # type: ignore[assignment]
    return raiser


def mock_llm_in_agent(monkeypatch, responses: Iterable[LLMResponse]) -> ScriptedLLM:
    """Patch ``CodingAgent.__init__`` so every constructed agent uses a
    ``ScriptedLLM`` with the given responses. Mirrors
    ``coding-harness/tests/test_cli.py`` exactly. Returns the script;
    note all agents constructed under the patch share the same script."""

    script = ScriptedLLM(responses)
    real_init = CodingAgent.__init__

    def patched_init(self, config):
        real_init(self, config)
        self.llm.complete = script.complete  # type: ignore[assignment]
        self.llm.stream = script.stream  # type: ignore[assignment]

    monkeypatch.setattr(CodingAgent, "__init__", patched_init)
    return script
