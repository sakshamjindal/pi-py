"""Steering and follow-up queues for programmatic message injection."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


class MessageQueue:
    """Async-safe FIFO. Mutations are awaited so SDK callers can `await
    handle.steer(...)` without managing locks themselves."""

    def __init__(self) -> None:
        self._items: list[str] = []
        self._lock = asyncio.Lock()

    async def put(self, content: str) -> None:
        async with self._lock:
            self._items.append(content)

    async def drain(self) -> list[str]:
        async with self._lock:
            items = list(self._items)
            self._items.clear()
            return items

    def empty(self) -> bool:
        return not self._items

    def __len__(self) -> int:
        return len(self._items)


@dataclass
class AgentHandle:
    """Returned by ``Agent.start(...)``. Lets the caller steer, follow up,
    abort, or continue the run after an error."""

    steering: MessageQueue
    follow_up: MessageQueue
    abort_event: asyncio.Event
    task: asyncio.Task
    # Bound to ``Agent.continue_run`` when constructed by ``Agent.start``.
    # Optional so the dataclass can still be hand-built in tests.
    continue_fn: Callable[[], Awaitable[Any]] | None = None

    async def steer(self, content: str) -> None:
        await self.steering.put(content)

    async def follow_up_msg(self, content: str) -> None:
        await self.follow_up.put(content)

    async def abort(self) -> None:
        self.abort_event.set()

    async def wait(self):
        return await self.task

    async def continue_run(self):
        """Resume the run without sending a new prompt. The previous
        ``task`` must already be done (e.g. it returned with reason
        ``error`` or ``aborted``). Returns the new ``RunResult``.
        """

        if self.continue_fn is None:
            raise RuntimeError(
                "continue_run is unavailable on this handle (build via Agent.start)"
            )
        if not self.task.done():
            raise RuntimeError("Cannot continue while the previous run is still in flight")
        # Reset abort so a fresh run can proceed.
        self.abort_event.clear()
        return await self.continue_fn()
