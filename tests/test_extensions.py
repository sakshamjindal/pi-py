"""Extensions: event bus, hooks, denial, replacement, loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyharness.events import LifecycleEvent
from pyharness.extensions import (
    EventBus,
    ExtensionAPI,
    HandlerContext,
    HookOutcome,
    HookResult,
    load_extensions,
)
from pyharness.tools.base import ToolRegistry


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


def test_load_extensions(tmp_path):
    ext_dir = tmp_path / "extensions"
    ext_dir.mkdir()
    (ext_dir / "myext.py").write_text(
        "from pyharness.extensions import HookOutcome\n"
        "def register(api):\n"
        "    api.on('e', _h)\n"
        "async def _h(event, ctx):\n"
        "    return HookOutcome.deny('blocked')\n",
        encoding="utf-8",
    )
    bus = EventBus()
    api = ExtensionAPI(bus=bus, registry=ToolRegistry(), settings=None)
    loaded = load_extensions(api, [ext_dir])
    assert "myext" in loaded.modules


@pytest.mark.asyncio
async def test_loaded_extension_can_deny(tmp_path):
    ext_dir = tmp_path / "extensions"
    ext_dir.mkdir()
    (ext_dir / "myext.py").write_text(
        "from pyharness.extensions import HookOutcome\n"
        "def register(api):\n"
        "    api.on('e', _h)\n"
        "async def _h(event, ctx):\n"
        "    return HookOutcome.deny('blocked')\n",
        encoding="utf-8",
    )
    bus = EventBus()
    api = ExtensionAPI(bus=bus, registry=ToolRegistry(), settings=None)
    load_extensions(api, [ext_dir])
    out = await bus.emit(LifecycleEvent(name="e"), _ctx())
    assert out.result is HookResult.Deny
