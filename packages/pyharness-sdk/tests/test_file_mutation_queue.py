"""Tests for FileMutationQueue: same-path serialisation, different-path
parallelism, realpath dedup."""

from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path

import pytest

from pyharness import FileMutationQueue


@pytest.mark.asyncio
async def test_same_path_serialises():
    """Two coroutines acquiring the same path: the second waits for the
    first. Verified by an order-marker that records who entered/exited
    and in what order."""

    queue = FileMutationQueue()
    target = Path("/tmp/some-shared-file.txt")
    events: list[str] = []

    async def writer(name: str, hold: float):
        async with queue.acquire(target):
            events.append(f"{name}:start")
            await asyncio.sleep(hold)
            events.append(f"{name}:end")

    # Start B slightly later so A enters first.
    await asyncio.gather(writer("A", 0.05), _delayed(writer, ("B", 0.01), 0.005))

    # Strict serialisation: A must finish before B starts.
    assert events == ["A:start", "A:end", "B:start", "B:end"], events


@pytest.mark.asyncio
async def test_different_paths_parallelise(tmp_path):
    """Two coroutines on different paths overlap in time: a shared
    'concurrently_running' counter must reach 2 before either releases."""

    queue = FileMutationQueue()
    counter = {"running": 0, "max": 0}
    barrier = asyncio.Event()

    async def writer(target: Path):
        async with queue.acquire(target):
            counter["running"] += 1
            counter["max"] = max(counter["max"], counter["running"])
            # Wait for the *other* coroutine to also enter, proving
            # concurrent ownership of the queue (just on different keys).
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(barrier.wait(), timeout=1.0)
            counter["running"] -= 1

    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"

    async def both():
        await asyncio.gather(
            writer(a),
            _release_barrier_when(barrier, lambda: counter["running"] >= 2),
            writer(b),
        )

    await asyncio.wait_for(both(), timeout=2.0)
    assert counter["max"] == 2, (
        f"expected both writers to overlap, got max concurrent={counter['max']}"
    )


@pytest.mark.asyncio
async def test_symlink_resolves_to_same_lock(tmp_path):
    """A path and a symlink pointing at the same target must share a
    lock so two writes via the symlink and the real path serialise."""

    target = tmp_path / "real.txt"
    target.write_text("seed", encoding="utf-8")
    link = tmp_path / "link.txt"
    os.symlink(target, link)

    queue = FileMutationQueue()
    events: list[str] = []

    async def write_via(path: Path, name: str, hold: float):
        async with queue.acquire(path):
            events.append(f"{name}:start")
            await asyncio.sleep(hold)
            events.append(f"{name}:end")

    await asyncio.gather(
        write_via(target, "real", 0.05),
        _delayed(write_via, (link, "link", 0.01), 0.005),
    )
    # Must serialise: real finishes before link starts.
    assert events == ["real:start", "real:end", "link:start", "link:end"], events


@pytest.mark.asyncio
async def test_nonexistent_path_still_locks(tmp_path):
    """Path normalisation works for files that don't exist yet (the
    common case for ``write`` to a fresh path)."""

    queue = FileMutationQueue()
    target = tmp_path / "not-yet.txt"
    assert not target.exists()

    events: list[str] = []

    async def writer(name: str, hold: float):
        async with queue.acquire(target):
            events.append(f"{name}:start")
            await asyncio.sleep(hold)
            events.append(f"{name}:end")

    await asyncio.gather(writer("A", 0.05), _delayed(writer, ("B", 0.01), 0.005))
    assert events == ["A:start", "A:end", "B:start", "B:end"], events


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _delayed(fn, args, delay):
    await asyncio.sleep(delay)
    await fn(*args)


async def _release_barrier_when(barrier: asyncio.Event, predicate):
    """Poll briefly until predicate returns True, then set the barrier.
    Used by the parallelism test to release writers once both are inside
    the queue concurrently."""

    for _ in range(200):
        if predicate():
            barrier.set()
            return
        await asyncio.sleep(0.005)
    barrier.set()  # fail-safe so the test exits
