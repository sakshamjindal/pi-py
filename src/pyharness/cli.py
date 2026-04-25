"""Command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from .config import Settings
from .events import (
    AssistantMessageEvent,
    SessionEndEvent,
    SessionStartEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    UserMessageEvent,
)
from .harness import Harness, HarnessConfig
from .session import Session


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pyharness",
        description="Run an LLM-driven agent task in the current workspace.",
    )
    p.add_argument("prompt", nargs="*", help="Task prompt. Joined into one string.")
    p.add_argument("--workspace", type=Path, default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--agent", default=None, help="Named agent to run.")
    p.add_argument("-p", "--print", action="store_true", help="Print final output to stdout (default).")
    p.add_argument("--json", action="store_true", help="Stream events as JSONL to stdout.")
    p.add_argument("--bare", action="store_true", help="Skip extensions, AGENTS.md, settings.")
    p.add_argument("-c", "--continue", dest="continue_", action="store_true", help="Continue most recent session in cwd.")
    p.add_argument("-r", "--recent", action="store_true", help="List recent sessions for cwd.")
    p.add_argument("--session", default=None, help="Resume a specific session by id.")
    p.add_argument("--fork", default=None, help="Fork a session by id.")
    p.add_argument("--at-event", type=int, default=None, help="With --fork: fork at this event sequence.")
    p.add_argument("--max-turns", type=int, default=None)
    p.add_argument("--no-stream", action="store_true", help="Collect output before printing (off by default).")
    p.add_argument("--quiet", action="store_true", help="Suppress non-final output.")
    # `pyharness sessions ...` is dispatched in main() before argparse runs.
    return p


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    # The top-level `sessions` subcommand has to be detected before argparse,
    # because the run command's positional `prompt` (nargs="*") would
    # otherwise greedily consume it.
    if raw and raw[0] == "sessions":
        return _handle_sessions_cli(raw[1:])

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.recent:
        return _list_sessions(Path.cwd(), n=20, all_dirs=False)

    workspace = (args.workspace or Path.cwd()).resolve()

    if args.continue_:
        recent = Session.list_recent(workspace, n=1)
        if not recent:
            sys.stderr.write("No previous session in this workspace.\n")
            return 2
        args.session = recent[0].session_id

    prompt = " ".join(args.prompt).strip()
    if not prompt:
        sys.stderr.write("error: no prompt provided.\n")
        return 2

    cli_overrides: dict[str, Any] = {}
    if args.model:
        cli_overrides["default_model"] = args.model
    if args.max_turns:
        cli_overrides["max_turns"] = args.max_turns

    settings = Settings.load(workspace=workspace, cli_overrides=cli_overrides)

    config = HarnessConfig(
        workspace=workspace,
        model=args.model,
        agent_name=args.agent,
        max_turns=args.max_turns,
        settings=settings,
        bare=args.bare,
        resume_from=args.session,
        fork_from=args.fork,
        fork_at_event=args.at_event,
        cli_overrides=cli_overrides,
    )

    return asyncio.run(_run(config, prompt, args))


async def _run(config: HarnessConfig, prompt: str, args: argparse.Namespace) -> int:
    harness = Harness(config)
    if args.json:
        _attach_json_stream(harness)
    elif not args.quiet:
        _attach_human_stream(harness)
    result = await harness.run(prompt)
    if args.json:
        sys.stdout.write(json.dumps({"type": "result", "data": result.model_dump()}) + "\n")
    else:
        sys.stdout.write(result.final_output.rstrip() + "\n")
        if not result.completed:
            sys.stderr.write(f"[run did not complete: {result.reason}]\n")
    return 0 if result.completed else 1


def _attach_json_stream(harness: Harness) -> None:
    bus = harness.event_bus

    async def handler(event, ctx):
        sys.stdout.write(
            json.dumps({"type": event.name, "payload": event.payload}, default=str) + "\n"
        )
        sys.stdout.flush()
        return None

    for name in (
        "session_start",
        "session_end",
        "turn_start",
        "turn_end",
        "before_tool_call",
        "after_tool_call",
        "after_llm_call",
        "compaction_end",
    ):
        bus.subscribe(name, handler)


def _attach_human_stream(harness: Harness) -> None:
    bus = harness.event_bus

    async def on_tool_start(event, ctx):
        name = event.payload.get("tool_name")
        sys.stderr.write(f"  → {name}\n")
        sys.stderr.flush()
        return None

    async def on_tool_end(event, ctx):
        ok = event.payload.get("ok")
        if not ok:
            sys.stderr.write(f"    [tool error]\n")
        return None

    async def on_session_end(event, ctx):
        reason = event.payload.get("reason")
        if reason and reason != "completed":
            sys.stderr.write(f"[end: {reason}]\n")
        return None

    bus.subscribe("before_tool_call", on_tool_start)
    bus.subscribe("after_tool_call", on_tool_end)
    bus.subscribe("session_end", on_session_end)


# ---------------------------------------------------------------------------
# `pyharness sessions ...`
# ---------------------------------------------------------------------------


def _handle_sessions(args: argparse.Namespace) -> int:
    if args.sessions_cmd == "ls":
        return _list_sessions(Path.cwd(), n=args.n, all_dirs=args.all)
    if args.sessions_cmd == "show":
        return _show_session(args.session_id)
    if args.sessions_cmd == "replay":
        return _replay_session(args.session_id)
    return 2


def _handle_sessions_cli(rest: list[str]) -> int:
    sub = argparse.ArgumentParser(prog="pyharness sessions")
    cmds = sub.add_subparsers(dest="sessions_cmd", required=True)
    s_ls = cmds.add_parser("ls")
    s_ls.add_argument("--all", action="store_true")
    s_ls.add_argument("-n", type=int, default=20)
    s_show = cmds.add_parser("show")
    s_show.add_argument("session_id")
    s_replay = cmds.add_parser("replay")
    s_replay.add_argument("session_id")
    args = sub.parse_args(rest)
    return _handle_sessions(args)


def _list_sessions(cwd: Path, *, n: int, all_dirs: bool) -> int:
    sessions = Session.list_recent(None if all_dirs else cwd, n=n)
    if not sessions:
        sys.stderr.write("(no sessions)\n")
        return 0
    for s in sessions:
        sys.stdout.write(
            f"{s.session_id}  {s.model or '?':30s}  {s.cwd}\n"
        )
    return 0


def _show_session(session_id: str) -> int:
    log = Session.find_log(session_id)
    if log is None:
        sys.stderr.write(f"No session: {session_id}\n")
        return 2
    with log.open("r", encoding="utf-8") as fh:
        for line in fh:
            sys.stdout.write(line)
    return 0


def _replay_session(session_id: str) -> int:
    log = Session.find_log(session_id)
    if log is None:
        sys.stderr.write(f"No session: {session_id}\n")
        return 2
    s = Session(session_id=session_id, cwd=Path.cwd(), log_path=log)
    for ev in s.read_events():
        if isinstance(ev, SessionStartEvent):
            sys.stdout.write(f"[start] cwd={ev.cwd} model={ev.model}\n")
        elif isinstance(ev, UserMessageEvent):
            sys.stdout.write(f"[user] {ev.content}\n")
        elif isinstance(ev, AssistantMessageEvent):
            if ev.text:
                sys.stdout.write(f"[asst] {ev.text}\n")
            for tc in ev.tool_calls or []:
                fn = tc.get("function", {})
                sys.stdout.write(f"  → {fn.get('name')}({fn.get('arguments')})\n")
        elif isinstance(ev, ToolCallEndEvent):
            status = "ok" if ev.ok else f"err:{ev.error}"
            sys.stdout.write(f"  ← {ev.tool_name} [{status}]\n")
        elif isinstance(ev, SessionEndEvent):
            sys.stdout.write(f"[end] {ev.reason}: {ev.final_message}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
