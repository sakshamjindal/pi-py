"""Supervisor / specialist via subprocess.

A supervisor agent decides which specialist to call. ``DESIGN.md``
explicitly refuses in-loop sub-agent delegation, so the supervisor
spawns specialists as **subprocesses** running ``pyharness`` from the
shell. The example below shows the shape of that handoff.

Run with::

    python examples/orchestration/supervisor.py
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

from coding_harness import (
    CodingAgent,
    CodingAgentConfig,
    Settings,
    agent_workspace,
)


def call_specialist(role: str, workspace: Path, prompt: str) -> str:
    """Run a named-agent specialist as a subprocess.

    Assumes ``pyharness`` CLI is on PATH and that the role is defined as
    ``.pyharness/agents/<role>.md`` somewhere up the scope hierarchy
    from ``workspace``.
    """

    cmd = [
        "pyharness",
        "--agent",
        role,
        "--workspace",
        str(workspace),
        prompt,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return f"[specialist {role!r} failed: {proc.stderr.strip()}]"
    return proc.stdout.strip()


async def run_supervisor(base: Path, request: str) -> str:
    """A toy supervisor: route by keyword. Real supervisors would
    delegate via a SpawnAgentTool the supervisor itself can call (see
    `tools/spawn_agent.py` recipe — not shipped, easy to write)."""

    async with agent_workspace(base, "supervisor", cleanup=False) as ws:
        if "research" in request.lower():
            return call_specialist("researcher", ws, request)
        if "review" in request.lower():
            return call_specialist("reviewer", ws, request)

        # Fall back to running the request inline.
        agent = CodingAgent(
            CodingAgentConfig(
                workspace=ws,
                settings=Settings(),
                extensions_enabled=[],
            )
        )
        result = await agent.run(request)
        return result.final_output


def main() -> None:
    base = Path("/tmp/pyharness-supervisor-demo")
    if base.exists():
        shutil.rmtree(base)
    print(asyncio.run(run_supervisor(base, "Summarise async generators in Python.")))


if __name__ == "__main__":
    main()
