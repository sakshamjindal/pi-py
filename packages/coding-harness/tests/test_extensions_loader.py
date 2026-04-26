"""Extension discovery + opt-in activation."""

from __future__ import annotations

from pathlib import Path

import pytest

from coding_harness import (
    discover_extensions,
    load_extensions,
)
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


def _write_ext(ext_dir: Path, name: str = "myext") -> None:
    ext_dir.mkdir(parents=True, exist_ok=True)
    (ext_dir / f"{name}.py").write_text(
        "from pyharness import HookOutcome\n"
        "def register(api):\n"
        "    api.on('e', _h)\n"
        "async def _h(event, ctx):\n"
        "    return HookOutcome.deny('blocked')\n",
        encoding="utf-8",
    )


def test_discover_lists_filesystem_extensions(tmp_path):
    ext_dir = tmp_path / "extensions"
    _write_ext(ext_dir, "myext")
    available = discover_extensions([ext_dir])
    assert "myext" in available.refs


def test_extensions_are_opt_in_no_enabled_means_no_load(tmp_path):
    """Discovery surfaces the extension; an empty enabled list activates
    nothing. This is the new default behavior."""

    ext_dir = tmp_path / "extensions"
    _write_ext(ext_dir, "myext")

    bus = EventBus()
    api = ExtensionAPI(bus=bus, registry=ToolRegistry(), settings=None)
    available = discover_extensions([ext_dir])
    loaded = load_extensions(api, available, enabled=[])
    assert loaded.modules == []


def test_load_extensions_when_explicitly_enabled(tmp_path):
    ext_dir = tmp_path / "extensions"
    _write_ext(ext_dir, "myext")

    bus = EventBus()
    api = ExtensionAPI(bus=bus, registry=ToolRegistry(), settings=None)
    available = discover_extensions([ext_dir])
    loaded = load_extensions(api, available, enabled=["myext"])
    assert "myext" in loaded.modules


@pytest.mark.asyncio
async def test_explicitly_enabled_extension_can_deny(tmp_path):
    ext_dir = tmp_path / "extensions"
    _write_ext(ext_dir, "myext")

    bus = EventBus()
    api = ExtensionAPI(bus=bus, registry=ToolRegistry(), settings=None)
    available = discover_extensions([ext_dir])
    load_extensions(api, available, enabled=["myext"])

    out = await bus.emit(LifecycleEvent(name="e"), _ctx())
    assert out.result is HookResult.Deny


def test_unknown_extension_name_is_skipped(tmp_path, capsys):
    ext_dir = tmp_path / "extensions"
    _write_ext(ext_dir, "myext")

    bus = EventBus()
    api = ExtensionAPI(bus=bus, registry=ToolRegistry(), settings=None)
    available = discover_extensions([ext_dir])
    loaded = load_extensions(api, available, enabled=["does-not-exist"])
    assert loaded.modules == []
    err = capsys.readouterr().err
    assert "does-not-exist" in err


def test_extra_register_fns_are_called(tmp_path):
    """Programmatic register() callables passed via extra_register_fns
    activate without needing a name."""

    bus = EventBus()
    api = ExtensionAPI(bus=bus, registry=ToolRegistry(), settings=None)

    captured = []

    def my_register(api):
        captured.append("called")

    available = discover_extensions([])
    loaded = load_extensions(api, available, enabled=[], extra_register_fns=[my_register])
    assert captured == ["called"]
    assert len(loaded.modules) == 1
