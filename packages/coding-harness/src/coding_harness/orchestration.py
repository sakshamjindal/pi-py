"""Tiny orchestration helpers.

The library deliberately ships no ``Pipeline`` / ``FanOut`` framework —
orchestration patterns are domain-shaped and best expressed as plain
Python. The single helper here, ``agent_workspace``, owns the boring
filesystem work that every multi-agent recipe needs: create a per-agent
directory, optionally clean it up.

See ``examples/orchestration/`` for full pipeline / fan-out / supervisor
recipes built on top of ``CodingAgent``.
"""

from __future__ import annotations

import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path


@asynccontextmanager
async def agent_workspace(
    base: Path,
    name: str,
    *,
    cleanup: bool = False,
) -> AsyncIterator[Path]:
    """Yield ``base / name`` after ensuring it exists.

    Set ``cleanup=True`` for ephemeral per-agent dirs (e.g. inside a
    request handler). The default is ``False`` so artefacts persist and
    can be passed to a downstream agent in the same workflow.
    """

    ws = (base / name).resolve()
    ws.mkdir(parents=True, exist_ok=True)
    try:
        yield ws
    finally:
        if cleanup:
            shutil.rmtree(ws, ignore_errors=True)
