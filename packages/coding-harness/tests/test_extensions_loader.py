"""Extension loader: file discovery + register() invocation."""

from __future__ import annotations

from pathlib import Path

import pytest

from coding_harness import load_extensions
from pyharness import (
    EventBus,
    ExtensionAPI,
    HandlerContext,
    HookResult,
    LifecycleEvent,
    ToolRegistry,
)


def _ctx() -> HandlerContext:
    return HandlerContext(settings=None, workspace=Path.cwd(), session_id="s", run_id="r")


def test_load_extensions(tmp_path):
    ext_dir = tmp_path / "extensions"
    ext_dir.mkdir()
    (ext_dir / "myext.py").write_text(
        "from pyharness import HookOutcome\n"
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
        "from pyharness import HookOutcome\n"
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
