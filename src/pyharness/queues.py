"""Steering and follow-up queues for programmatic message injection."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


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
class HarnessHandle:
    """Returned by ``Harness.start(...)``. Lets the caller steer or
    follow-up while the run is in flight."""

    steering: MessageQueue
    follow_up: MessageQueue
    abort_event: asyncio.Event
    task: asyncio.Task

    async def steer(self, content: str) -> None:
        await self.steering.put(content)

    async def follow_up_msg(self, content: str) -> None:
        await self.follow_up.put(content)

    async def abort(self) -> None:
        self.abort_event.set()

    async def wait(self):
        return await self.task
