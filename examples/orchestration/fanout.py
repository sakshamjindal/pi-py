"""Fan-out / fan-in: spawn N agents in parallel, reduce their outputs.

Each agent gets its own workspace and runs concurrently via asyncio.
A simple reducer joins the results at the end.

Run with::

    python examples/orchestration/fanout.py
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from coding_harness import (
    CodingAgent,
    CodingAgentConfig,
    Settings,
    agent_workspace,
)


async def run_one(base: Path, idx: int, prompt: str) -> str:
    async with agent_workspace(base, f"worker-{idx}", cleanup=True) as ws:
        agent = CodingAgent(
            CodingAgentConfig(
                workspace=ws,
                settings=Settings(),
                extensions_enabled=[],
            )
        )
        result = await agent.run(prompt)
        return result.final_output


async def fanout(base: Path, prompts: list[str]) -> list[str]:
    return await asyncio.gather(
        *(run_one(base, i, p) for i, p in enumerate(prompts))
    )


def reduce_outputs(results: list[str]) -> str:
    return "\n\n---\n\n".join(f"# Worker {i}\n\n{r}" for i, r in enumerate(results))


def main() -> None:
    base = Path("/tmp/pyharness-fanout-demo")
    if base.exists():
        shutil.rmtree(base)

    prompts = [
        "Summarise the merge sort algorithm in 3 sentences.",
        "Summarise the quicksort algorithm in 3 sentences.",
        "Summarise the heapsort algorithm in 3 sentences.",
    ]
    results = asyncio.run(fanout(base, prompts))
    print(reduce_outputs(results))


if __name__ == "__main__":
    main()
