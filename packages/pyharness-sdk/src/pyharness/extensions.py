"""Extension runtime: event bus, hook outcomes, ExtensionAPI.

This module is the *runtime* surface of the extension system. The
file-discovery loader (`load_extensions`) lives in the harness package,
because finding `~/.pyharness/extensions/*.py` is an application
concern, not a kernel concern. The SDK only knows about events, hooks,
and the API contract that extensions code against.
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import inspect
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
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
    def cont(cls) -> HookOutcome:
        return cls(result=HookResult.Continue)

    @classmethod
    def deny(cls, reason: str) -> HookOutcome:
        return cls(result=HookResult.Deny, reason=reason)

    @classmethod
    def modify(cls, new_event: LifecycleEvent) -> HookOutcome:
        return cls(result=HookResult.Modify, new_event=new_event)

    @classmethod
    def replace(cls, value: Any) -> HookOutcome:
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

    async def emit(self, event: LifecycleEvent, ctx: HandlerContext) -> HookOutcome:
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
                sys.stderr.write(f"[extension] handler for {event.name} raised: {exc}\n")
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
        with contextlib.suppress(RuntimeError):
            asyncio.get_event_loop().create_task(self._session_appender(entry))

    def get_setting(self, key: str, default: Any = None) -> Any:
        if self._settings is None:
            return default
        if hasattr(self._settings, key):
            return getattr(self._settings, key)
        if isinstance(self._settings, dict):
            return self._settings.get(key, default)
        return default
