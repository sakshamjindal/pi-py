"""Per-path async lock for file mutations.

When tool calls run in parallel (``tool_execution="parallel"``), two
``write`` or ``edit`` calls targeting the same file would race the
filesystem — ordering of bytes on disk depends on which coroutine
hits the syscall first, and the resulting state is non-deterministic.

The queue serialises mutations to the *same* path while leaving
mutations to *different* paths free to run concurrently. Mirrors the
``withFileMutationQueue`` primitive in pi-mono's coding-agent.

Resolution rules:

- Paths are normalised via ``Path.resolve(strict=False)`` so symlinks
  pointing at the same target share a lock.
- Non-existent target paths still get a stable lock based on their
  normalised form (``write`` to a path that doesn't yet exist needs
  the same protection as one that does).

Construct one queue per ``Agent``; tools access it via
``ToolContext.extras["file_mutation_queue"]``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path


class FileMutationQueue:
    """Async lock keyed on resolved file paths.

    Acquire via ``async with queue.acquire(path):``. Idempotent
    re-acquisition from the same coroutine is NOT supported (asyncio
    locks aren't reentrant); a tool that wants to read-modify-write
    should hold the lock for the whole window.
    """

    def __init__(self) -> None:
        self._locks: dict[Path, asyncio.Lock] = {}
        # Guards ``_locks`` mutation so two coroutines can't race to
        # create the same key. The work *inside* the per-path lock
        # never holds this meta-lock.
        self._meta = asyncio.Lock()

    @asynccontextmanager
    async def acquire(self, path: str | Path) -> AsyncIterator[None]:
        """Hold the lock for ``path`` until the body exits.

        ``path`` may be relative; it's normalised via ``Path.resolve``
        so callers don't have to.
        """

        key = self._key(path)
        lock = await self._get_or_create_lock(key)
        async with lock:
            yield

    async def _get_or_create_lock(self, key: Path) -> asyncio.Lock:
        async with self._meta:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock

    @staticmethod
    def _key(path: str | Path) -> Path:
        # ``strict=False`` so non-existent files resolve correctly
        # (write-to-new-path is the most common case for ``write``).
        return Path(path).resolve(strict=False)
