"""Extension runtime: event bus, hooks, denial, replacement.

The file-discovery loader (`load_extensions`) lives in the harness
package and is exercised in ``packages/harness/tests/test_extensions_loader.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pyharness import (
    EventBus,
    HandlerContext,
    HookOutcome,
    HookResult,
    LifecycleEvent,
)


def _ctx() -> HandlerContext:
    return HandlerContext(settings=None, workspace=Path.cwd(), session_id="s", run_id="r")


@pytest.mark.asyncio
async def test_subscribe_and_emit():
    seen = []

    async def handler(event, ctx):
        seen.append(event.name)
        return HookOutcome.cont()

    bus = EventBus()
    bus.subscribe("foo", handler)
    out = await bus.emit(LifecycleEvent(name="foo"), _ctx())
    assert out.result is HookResult.Continue
    assert seen == ["foo"]


@pytest.mark.asyncio
async def test_deny_short_circuits():
    async def deny(event, ctx):
        return HookOutcome.deny("nope")

    bus = EventBus()
    bus.subscribe("e", deny)
    out = await bus.emit(LifecycleEvent(name="e"), _ctx())
    assert out.result is HookResult.Deny
    assert out.reason == "nope"


@pytest.mark.asyncio
async def test_replace_short_circuits():
    async def repl(event, ctx):
        return HookOutcome.replace({"answer": 42})

    bus = EventBus()
    bus.subscribe("e", repl)
    out = await bus.emit(LifecycleEvent(name="e"), _ctx())
    assert out.result is HookResult.Replace
    assert out.replacement_value == {"answer": 42}


@pytest.mark.asyncio
async def test_handler_exception_is_swallowed():
    async def boom(event, ctx):
        raise RuntimeError("oops")

    bus = EventBus()
    bus.subscribe("e", boom)
    out = await bus.emit(LifecycleEvent(name="e"), _ctx())
    assert out.result is HookResult.Continue
