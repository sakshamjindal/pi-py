"""Minimal interactive TUI for dogfooding the pyharness agent.

A REPL: read a line, run the coding agent, print the result, repeat.
Pass a prompt as argv for one-shot mode. Type ``exit`` / ``quit`` or
hit Ctrl-D to leave the REPL.

Stdlib only. Tool calls are traced to stderr as ``  → <tool_name>``
so you can see what the agent is doing without a noisy event stream.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from coding_harness import CodingAgent, CodingAgentConfig


def _attach_trace(agent: CodingAgent) -> None:
    async def on_tool_start(event, ctx):
        sys.stderr.write(f"  → {event.payload.get('tool_name')}\n")
        sys.stderr.flush()
        return None

    agent.event_bus.subscribe("before_tool_call", on_tool_start)


async def _run_once(prompt: str, workspace: Path) -> int:
    agent = CodingAgent(CodingAgentConfig(workspace=workspace))
    _attach_trace(agent)
    result = await agent.run(prompt)
    sys.stdout.write((result.final_output or "").rstrip() + "\n")
    sys.stdout.flush()
    return 0 if result.completed else 1


async def _repl(workspace: Path) -> int:
    sys.stderr.write("pyharness-tui — type a prompt, ctrl-d to quit.\n")
    sys.stderr.flush()
    while True:
        try:
            prompt = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            sys.stderr.write("\n")
            return 0
        if not prompt:
            continue
        if prompt in ("exit", "quit"):
            return 0
        await _run_once(prompt, workspace)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    workspace = Path.cwd()
    if args:
        return asyncio.run(_run_once(" ".join(args), workspace))
    return asyncio.run(_repl(workspace))


if __name__ == "__main__":
    raise SystemExit(main())
