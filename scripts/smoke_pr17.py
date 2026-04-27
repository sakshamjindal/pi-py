"""Smoke tests for PR #17 against OpenRouter.

Runs three scenarios end-to-end with a real model:

1. Parallel vs sequential tool dispatch — tee up multiple parallel-safe
   reads, compare wall time.
2. Terminate signal — register a ``done`` tool that sets terminate=True,
   confirm the run exits in one turn.
3. continue_run — force an LLM error on the first call, then resume and
   confirm recovery.

Run:
    cd /Users/sakshamjindal/work/pi-py
    set -a; source .env; set +a
    python scripts/smoke_pr17.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

# Make the SDK importable when running from the repo without an editable install.
ROOT = Path(__file__).resolve().parent.parent
for pkg in ("pyharness-sdk", "coding-harness"):
    src = ROOT / "packages" / pkg / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))

from pydantic import BaseModel, Field

from pyharness import (  # noqa: E402
    Agent,
    AgentOptions,
    EventBus,
    LLMClient,
    LLMResponse,
    Session,
    Tool,
    ToolContext,
    ToolRegistry,
    ToolResult,
)

MODEL = os.environ.get("PI_SMOKE_MODEL", "openrouter/anthropic/claude-sonnet-4.5")


# ---------------------------------------------------------------------------
# Test tools
# ---------------------------------------------------------------------------


class _ReadArgs(BaseModel):
    path: str = Field(description="Path relative to the workspace.")


class ReadTool(Tool):
    name = "read_file"
    description = "Read a small text file from the workspace and return its contents."
    args_schema = _ReadArgs

    async def execute(self, args: _ReadArgs, ctx: ToolContext):  # type: ignore[override]
        # Artificial delay sized to dominate the LLM round-trip jitter so the
        # parallel speedup is measurable end-to-end. Real reads are ~1ms; we
        # pretend the workspace is on a slow filesystem.
        await asyncio.sleep(1.5)
        target = ctx.workspace / args.path
        if not target.is_file():
            return f"missing: {args.path}"
        return target.read_text(encoding="utf-8")


class _DoneArgs(BaseModel):
    reason: str = Field(description="Short reason the task is complete.")


class DoneTool(Tool):
    name = "done"
    description = (
        "Call this when the task is complete. Returns terminate=True so the "
        "agent stops without one more LLM round-trip."
    )
    args_schema = _DoneArgs

    async def execute(self, args: _DoneArgs, ctx: ToolContext):  # type: ignore[override]
        return ToolResult(content=f"acknowledged: {args.reason}", terminate=True)


# ---------------------------------------------------------------------------
# Workspace fixture: a few tiny files for the model to read
# ---------------------------------------------------------------------------


def make_workspace(base: Path) -> Path:
    ws = base / "smoke_ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "alpha.txt").write_text("alpha line\n", encoding="utf-8")
    (ws / "bravo.txt").write_text("bravo line\n", encoding="utf-8")
    (ws / "charlie.txt").write_text("charlie line\n", encoding="utf-8")
    return ws


def build_agent(
    *,
    workspace: Path,
    sessions_dir: Path,
    tool_execution: str,
    tools: list[Tool],
    system_prompt: str,
    llm: LLMClient | None = None,
) -> Agent:
    registry = ToolRegistry()
    for t in tools:
        registry.register(t)
    session = Session.new(workspace, base_dir=sessions_dir)
    return Agent(
        AgentOptions(
            model=MODEL,
            max_turns=8,
            tool_execution=tool_execution,  # type: ignore[arg-type]
        ),
        system_prompt=system_prompt,
        tool_registry=registry,
        session=session,
        event_bus=EventBus(),
        workspace=workspace,
        llm=llm or LLMClient(),
    )


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


PROMPT_THREE_READS = (
    "Read the contents of alpha.txt, bravo.txt, and charlie.txt using the "
    "read_file tool. Issue all three tool calls in your single response. "
    "Then summarise what you found in one sentence."
)


async def scenario_parallel(workspace: Path, sessions_dir: Path) -> dict:
    print("\n=== (1) parallel tool dispatch ===")
    agent = build_agent(
        workspace=workspace,
        sessions_dir=sessions_dir,
        tool_execution="parallel",
        tools=[ReadTool()],
        system_prompt="You are a helpful agent.",
    )
    started = time.perf_counter()
    result = await agent.run(PROMPT_THREE_READS)
    elapsed = time.perf_counter() - started
    print(f"  elapsed     : {elapsed:.2f}s")
    print(f"  reason      : {result.reason}")
    print(f"  turns       : {result.turn_count}")
    print(f"  output[:80] : {result.final_output[:80]!r}")
    return {"elapsed": elapsed, "reason": result.reason, "turns": result.turn_count}


async def scenario_sequential(workspace: Path, sessions_dir: Path) -> dict:
    print("\n=== (1b) sequential tool dispatch (baseline) ===")
    agent = build_agent(
        workspace=workspace,
        sessions_dir=sessions_dir,
        tool_execution="sequential",
        tools=[ReadTool()],
        system_prompt="You are a helpful agent.",
    )
    started = time.perf_counter()
    result = await agent.run(PROMPT_THREE_READS)
    elapsed = time.perf_counter() - started
    print(f"  elapsed     : {elapsed:.2f}s")
    print(f"  reason      : {result.reason}")
    print(f"  turns       : {result.turn_count}")
    return {"elapsed": elapsed, "reason": result.reason, "turns": result.turn_count}


async def scenario_terminate(workspace: Path, sessions_dir: Path) -> dict:
    print("\n=== (2) terminate signal ===")
    agent = build_agent(
        workspace=workspace,
        sessions_dir=sessions_dir,
        tool_execution="sequential",
        tools=[DoneTool()],
        system_prompt=(
            "You are a task-completion agent. When the user gives you any "
            "task, immediately call the `done` tool with a one-sentence reason. "
            "Do NOT produce additional commentary after calling `done`."
        ),
    )
    result = await agent.run("Confirm receipt and stop. Use the done tool.")
    print(f"  reason      : {result.reason}")
    print(f"  turns       : {result.turn_count}  (expected 1 — terminate=True short-circuits)")
    print(f"  completed   : {result.completed}")
    print(f"  output[:80] : {result.final_output[:80]!r}")
    return {"reason": result.reason, "turns": result.turn_count, "completed": result.completed}


class _OneShotFailingLLM:
    """Fails the first call, delegates to a real LLMClient afterwards."""

    def __init__(self, real: LLMClient):
        self._real = real
        self._failed_once = False

    async def complete(self, **kwargs) -> LLMResponse:
        if not self._failed_once:
            self._failed_once = True
            raise RuntimeError("simulated transient network error")
        return await self._real.complete(**kwargs)

    async def stream(self, **kwargs):  # pragma: no cover
        async for ev in self._real.stream(**kwargs):
            yield ev


async def scenario_continue(workspace: Path, sessions_dir: Path) -> dict:
    print("\n=== (3) continue_run after error ===")
    agent = build_agent(
        workspace=workspace,
        sessions_dir=sessions_dir,
        tool_execution="sequential",
        tools=[],
        system_prompt="You are a helpful agent. Reply concisely.",
        llm=_OneShotFailingLLM(LLMClient()),  # type: ignore[arg-type]
    )
    first = await agent.run("Say a short hello in five words or fewer.")
    print(f"  first.reason     : {first.reason}  (expected 'error')")
    print(f"  first.turns      : {first.turn_count}")
    second = await agent.continue_run()
    print(f"  second.reason    : {second.reason}  (expected 'completed')")
    print(f"  second.turns     : {second.turn_count}")
    print(f"  second.output    : {second.final_output[:80]!r}")
    return {
        "first_reason": first.reason,
        "second_reason": second.reason,
        "second_output": second.final_output,
    }


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


async def main() -> int:
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY not set; aborting.", file=sys.stderr)
        return 2

    print(f"model: {MODEL}")
    base = Path("/tmp/pi-py-smoke")
    base.mkdir(parents=True, exist_ok=True)
    sessions_dir = base / "sessions"
    workspace = make_workspace(base)

    parallel_stats = await scenario_parallel(workspace, sessions_dir)
    sequential_stats = await scenario_sequential(workspace, sessions_dir)
    terminate_stats = await scenario_terminate(workspace, sessions_dir)
    continue_stats = await scenario_continue(workspace, sessions_dir)

    print("\n=== summary ===")
    speedup = (
        sequential_stats["elapsed"] / parallel_stats["elapsed"]
        if parallel_stats["elapsed"]
        else float("nan")
    )
    print(
        f"  parallel/sequential elapsed: {parallel_stats['elapsed']:.2f}s / "
        f"{sequential_stats['elapsed']:.2f}s  (speedup: {speedup:.2f}×)"
    )
    print(
        f"  terminate: turns={terminate_stats['turns']} reason={terminate_stats['reason']}"
    )
    print(
        f"  continue : first={continue_stats['first_reason']} "
        f"second={continue_stats['second_reason']}"
    )

    ok = (
        parallel_stats["reason"] == "completed"
        and sequential_stats["reason"] == "completed"
        and terminate_stats["reason"] == "completed"
        and terminate_stats["turns"] == 1
        and continue_stats["first_reason"] == "error"
        and continue_stats["second_reason"] == "completed"
    )
    print("OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
