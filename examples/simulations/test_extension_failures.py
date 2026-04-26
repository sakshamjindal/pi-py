"""Extension failure scenarios.

Covers the silent-error paths in
``coding-harness/extensions_loader.py`` and the deny / hook-handler
behaviours in ``pyharness-sdk/extensions.py``. Most failures should
surface as stderr messages without crashing the run.
"""

from __future__ import annotations

import pytest

from coding_harness import (
    CodingAgent,
    CodingAgentConfig,
    Settings,
    discover_extensions,
    load_extensions,
)
from pyharness import (
    EventBus,
    ExtensionAPI,
    HandlerContext,
    HookOutcome,
    HookResult,
    LifecycleEvent,
    LLMResponse,
    ToolRegistry,
)

from ._helpers import install_scripted_llm, make_project


def _ctx(tmp_path):
    return HandlerContext(settings=None, workspace=tmp_path, session_id="s", run_id="r")


def test_extension_with_syntax_error_is_skipped(tmp_path, capsys):
    """A .py file that fails to import must NOT crash discovery.
    Activation logs to stderr and skips."""

    ext_dir = tmp_path / ".pyharness" / "extensions"
    ext_dir.mkdir(parents=True)
    (ext_dir / "broken.py").write_text("this is not valid python (((", encoding="utf-8")

    available = discover_extensions([ext_dir])
    # Discovery is metadata-only — the syntax error doesn't surface yet.
    assert "broken" in available.refs

    bus = EventBus()
    api = ExtensionAPI(bus=bus, registry=ToolRegistry(), settings=None)
    loaded = load_extensions(api, available, enabled=["broken"])
    assert loaded.modules == []
    err = capsys.readouterr().err
    assert "broken" in err  # message goes to stderr


def test_extension_register_raises(tmp_path, capsys):
    """A clean import but a raising ``register(api)`` is logged and
    activation is skipped."""

    ext_dir = tmp_path / ".pyharness" / "extensions"
    ext_dir.mkdir(parents=True)
    (ext_dir / "throwing.py").write_text(
        "def register(api):\n    raise RuntimeError('register exploded')\n",
        encoding="utf-8",
    )

    bus = EventBus()
    api = ExtensionAPI(bus=bus, registry=ToolRegistry(), settings=None)
    available = discover_extensions([ext_dir])
    loaded = load_extensions(api, available, enabled=["throwing"])
    assert loaded.modules == []
    err = capsys.readouterr().err
    assert "register exploded" in err or "register()" in err


@pytest.mark.asyncio
async def test_extension_hook_handler_raises_does_not_crash_loop(tmp_path):
    """A handler that raises during dispatch is logged and the bus
    keeps going. ``EventBus.emit`` returns ``Continue`` if no handler
    produces a non-Continue outcome."""

    bus = EventBus()
    api = ExtensionAPI(bus=bus, registry=ToolRegistry(), settings=None)

    async def bad(event, ctx):
        raise RuntimeError("handler failed")

    api.on("e", bad)
    out = await bus.emit(LifecycleEvent(name="e"), _ctx(tmp_path))
    assert out.result is HookResult.Continue


@pytest.mark.asyncio
async def test_extension_denies_before_llm_call(tmp_path, isolated_session_dir):
    """Extension that returns ``HookOutcome.deny`` for ``before_llm_call``
    must terminate the run with reason='error' and a clear message."""

    workspace = make_project(tmp_path)

    def gate(api):
        async def deny(event, ctx):
            return HookOutcome.deny("blocked by policy")

        api.on("before_llm_call", deny)

    agent = CodingAgent(
        CodingAgentConfig(
            workspace=workspace,
            settings=Settings(),
            extra_extensions=[gate],
        )
    )
    install_scripted_llm(agent, [LLMResponse(text="never reached")])
    result = await agent.run("anything")
    assert result.completed is False
    assert result.reason == "error"
    assert "blocked by policy" in (result.final_output or "")


@pytest.mark.asyncio
async def test_extension_first_non_continue_wins(tmp_path):
    """Multiple handlers on the same event: the first non-Continue
    outcome short-circuits and wins."""

    bus = EventBus()
    api = ExtensionAPI(bus=bus, registry=ToolRegistry(), settings=None)
    calls: list[str] = []

    async def first(event, ctx):
        calls.append("first")
        return HookOutcome.cont()

    async def second(event, ctx):
        calls.append("second")
        return HookOutcome.deny("second wins")

    async def third(event, ctx):
        calls.append("third")
        return HookOutcome.deny("never reached")

    api.on("e", first)
    api.on("e", second)
    api.on("e", third)

    out = await bus.emit(LifecycleEvent(name="e"), _ctx(tmp_path))
    assert out.result is HookResult.Deny
    assert out.reason == "second wins"
    # Third must NOT have been called.
    assert calls == ["first", "second"]
