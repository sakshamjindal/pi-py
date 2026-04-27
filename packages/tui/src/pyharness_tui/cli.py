"""Minimal interactive TUI for dogfooding the pyharness agent.

A REPL: read a line, run the coding agent, print the result, repeat.
Pass a prompt as argv for one-shot mode. Type ``exit`` / ``quit`` or
hit Ctrl-D to leave the REPL.

Stdlib only. Tool calls are traced to stderr (``  → tool_name`` on
start, ``    [error]`` on failure) so you can see what the agent is
doing without a noisy event stream.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from coding_harness import CodingAgent, CodingAgentConfig, NoProjectError


def _attach_trace(agent: CodingAgent) -> None:
    async def on_tool_start(event, ctx):
        sys.stderr.write(f"  → {event.payload.get('tool_name')}\n")
        sys.stderr.flush()
        return None

    async def on_tool_end(event, ctx):
        if not event.payload.get("ok", True):
            err = event.payload.get("error") or "error"
            sys.stderr.write(f"    [{err}]\n")
            sys.stderr.flush()
        return None

    agent.event_bus.subscribe("before_tool_call", on_tool_start)
    agent.event_bus.subscribe("after_tool_call", on_tool_end)


def _build_agent(
    workspace: Path,
    *,
    bare: bool,
    model: str | None,
    resume_from: str | None = None,
) -> CodingAgent | None:
    """Construct the agent or print a friendly message and return None on
    NoProjectError. ``resume_from`` carries forward a session_id so the
    REPL maintains conversation history across prompts."""

    try:
        return CodingAgent(
            CodingAgentConfig(
                workspace=workspace,
                model=model,
                bare=bare,
                resume_from=resume_from,
            )
        )
    except NoProjectError as exc:
        sys.stderr.write(f"{exc}\n")
        return None


async def _run_once(prompt: str, agent: CodingAgent) -> int:
    _attach_trace(agent)
    result = await agent.run(prompt)
    sys.stdout.write((result.final_output or "").rstrip() + "\n")
    sys.stdout.flush()
    # Always print a per-turn summary so the user sees turns + reason
    # even when cost is unavailable (e.g. the OpenRouter route returns
    # tokens but no cost). For richer per-call audit trails (token
    # counts, latency, etc.) install the cost_logger extension and
    # check `.pyharness/cost.jsonl`.
    parts = [f"turns={result.turn_count}", f"reason={result.reason}"]
    if result.cost > 0:
        parts.append(f"cost=${result.cost:.4f}")
    sys.stderr.write(f"[{' '.join(parts)}]\n")
    return 0 if result.completed else 1


async def _repl(workspace: Path, *, bare: bool, model: str | None) -> int:
    sys.stderr.write(
        f"pyharness-tui — workspace={workspace} model={model or '(default)'}"
        f"{' [bare]' if bare else ''}\n"
        "Type a prompt, `exit`/`quit`, or Ctrl-D to leave.\n"
    )
    sys.stderr.flush()

    # Carry session_id across REPL turns so the model keeps full prior
    # context. First prompt builds a fresh session; turns 2+ resume the
    # same session (loop reads prior messages from the JSONL log on
    # construction). Without this, every REPL prompt was an independent
    # session with no memory of the previous one.
    session_id: str | None = None

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
        agent = _build_agent(workspace, bare=bare, model=model, resume_from=session_id)
        if agent is None:
            return 2
        await _run_once(prompt, agent)
        session_id = agent.session.session_id


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pyharness-tui",
        description="Minimal stdlib REPL for the coding-harness agent.",
    )
    p.add_argument("prompt", nargs="*", help="One-shot prompt. Empty → REPL mode.")
    p.add_argument(
        "--workspace", type=Path, default=None, help="Operating directory (default: cwd)."
    )
    p.add_argument("--model", default=None, help="Override default model.")
    p.add_argument(
        "--bare",
        action="store_true",
        help="Skip extensions / AGENTS.md / settings; bypass the .pyharness/ marker requirement.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    workspace = (args.workspace or Path.cwd()).resolve()

    if args.prompt:
        agent = _build_agent(workspace, bare=args.bare, model=args.model)
        if agent is None:
            return 2
        return asyncio.run(_run_once(" ".join(args.prompt), agent))

    return asyncio.run(_repl(workspace, bare=args.bare, model=args.model))


if __name__ == "__main__":
    raise SystemExit(main())
