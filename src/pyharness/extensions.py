"""Extension API and loader.

The contract: an extension is a Python module exposing a top-level
``register(api)`` function. The ``ExtensionAPI`` lets it subscribe to
lifecycle events, register or replace tools, and read the merged settings.

Lifecycle events are emitted by the harness through ``EventBus.emit``.
Handlers return ``HookOutcome``s; the first non-Continue outcome wins.
"""

from __future__ import annotations

import asyncio
import enum
import importlib.util
import inspect
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .events import LifecycleEvent
from .tools.base import Tool, ToolRegistry


class HookResult(enum.Enum):
    Continue = "continue"
    Deny = "deny"
    Modify = "modify"
    Replace = "replace"


@dataclass
class HookOutcome:
    result: HookResult
    reason: str | None = None
    new_event: LifecycleEvent | None = None
    replacement_value: Any = None

    @classmethod
    def cont(cls) -> "HookOutcome":
        return cls(result=HookResult.Continue)

    @classmethod
    def deny(cls, reason: str) -> "HookOutcome":
        return cls(result=HookResult.Deny, reason=reason)

    @classmethod
    def modify(cls, new_event: LifecycleEvent) -> "HookOutcome":
        return cls(result=HookResult.Modify, new_event=new_event)

    @classmethod
    def replace(cls, value: Any) -> "HookOutcome":
        return cls(result=HookResult.Replace, replacement_value=value)


@dataclass
class HandlerContext:
    settings: Any
    workspace: Path
    session_id: str
    run_id: str


HookHandler = Callable[[LifecycleEvent, HandlerContext], Awaitable[HookOutcome | None]]


class EventBus:
    """Async event bus. Handlers run in registration order."""

    def __init__(self):
        self._handlers: dict[str, list[HookHandler]] = {}

    def subscribe(self, event_name: str, handler: HookHandler) -> None:
        self._handlers.setdefault(event_name, []).append(handler)

    async def emit(
        self, event: LifecycleEvent, ctx: HandlerContext
    ) -> HookOutcome:
        """Dispatch the event. Returns the first non-Continue outcome, or
        Continue if every handler is a no-op."""

        handlers = list(self._handlers.get(event.name, ()))
        for handler in handlers:
            try:
                result = handler(event, ctx)
                if inspect.isawaitable(result):
                    result = await result
            except Exception as exc:
                # Extensions never crash the harness — log and continue.
                sys.stderr.write(
                    f"[extension] handler for {event.name} raised: {exc}\n"
                )
                continue
            if result is None:
                continue
            if not isinstance(result, HookOutcome):
                continue
            if result.result is not HookResult.Continue:
                return result
            if result.new_event is not None:
                event = result.new_event
        return HookOutcome.cont()


class ExtensionAPI:
    """The stable contract extensions code against."""

    def __init__(
        self,
        *,
        bus: EventBus,
        registry: ToolRegistry,
        settings: Any,
        session_appender: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ):
        self._bus = bus
        self._registry = registry
        self._settings = settings
        self._session_appender = session_appender

    def on(self, event_name: str, handler: HookHandler) -> None:
        self._bus.subscribe(event_name, handler)

    def register_tool(self, tool: Tool) -> None:
        if self._registry.has(tool.name):
            self._registry.replace(tool.name, tool)
        else:
            self._registry.register(tool)

    def replace_tool(self, name: str, tool: Tool) -> None:
        self._registry.replace(name, tool)

    def append_session_entry(self, entry: dict[str, Any]) -> None:
        if self._session_appender is None:
            return
        try:
            asyncio.get_event_loop().create_task(self._session_appender(entry))
        except RuntimeError:
            pass

    def get_setting(self, key: str, default: Any = None) -> Any:
        if self._settings is None:
            return default
        if hasattr(self._settings, key):
            return getattr(self._settings, key)
        if isinstance(self._settings, dict):
            return self._settings.get(key, default)
        return default


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


@dataclass
class LoadedExtensions:
    modules: list[str] = field(default_factory=list)


def _import_path(path: Path, name_hint: str) -> Any | None:
    if path.is_dir():
        init = path / "__init__.py"
        if not init.is_file():
            return None
        target = init
    else:
        target = path
    spec_name = f"pyharness_ext_{name_hint}"
    spec = importlib.util.spec_from_file_location(spec_name, target)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.stderr.write(f"[extension] failed to load {target}: {exc}\n")
        return None
    return module


def load_extensions(
    api: ExtensionAPI,
    extension_dirs: list[Path],
) -> LoadedExtensions:
    """Walk extension directories and load each module's ``register(api)``.

    Project-local extensions override personal ones with the same name (we
    keep the last registration to win, matching the merge order: home,
    then project, then workspace).
    """

    by_name: dict[str, Path] = {}
    for d in extension_dirs:
        if not d.is_dir():
            continue
        for entry in sorted(d.iterdir()):
            if entry.name.startswith("_") or entry.name.startswith("."):
                continue
            if entry.is_file() and entry.suffix == ".py":
                by_name[entry.stem] = entry
            elif entry.is_dir() and (entry / "__init__.py").is_file():
                by_name[entry.name] = entry

    loaded = LoadedExtensions()
    for name, path in by_name.items():
        module = _import_path(path, name)
        if module is None:
            continue
        register = getattr(module, "register", None)
        if not callable(register):
            continue
        try:
            register(api)
        except Exception as exc:
            sys.stderr.write(f"[extension] {name} register() raised: {exc}\n")
            continue
        loaded.modules.append(name)
    return loaded
