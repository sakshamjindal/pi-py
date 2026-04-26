"""Sequential pipeline: agent A → produces artefacts → agent B reads them.

Each agent gets its own per-task workspace. The pipeline orchestrator
owns a shared ``artefacts/`` directory that every agent can read from
and the producer agents write into.

This is plain Python — no framework. Run it with::

    python examples/orchestration/pipeline.py

Adapt freely; the only library dependency is ``CodingAgent`` and the
``agent_workspace`` helper.
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


async def run_pipeline(base: Path, topic: str) -> str:
    artefacts = base / "artefacts"
    artefacts.mkdir(parents=True, exist_ok=True)

    # Stage 1: research agent writes findings to a shared artefacts dir.
    async with agent_workspace(base, "research", cleanup=False) as ws:
        researcher = CodingAgent(
            CodingAgentConfig(
                workspace=ws,
                settings=Settings(),
                # Lock down extensions for this stage.
                extensions_enabled=[],
            )
        )
        await researcher.run(
            f"Research {topic}. Write a structured summary to "
            f"{artefacts}/findings.md. Cite sources."
        )

    # Stage 2: writer agent reads the artefact and produces the final report.
    async with agent_workspace(base, "writer", cleanup=False) as ws:
        writer = CodingAgent(
            CodingAgentConfig(
                workspace=ws,
                settings=Settings(),
                extensions_enabled=[],
            )
        )
        result = await writer.run(
            f"Read {artefacts}/findings.md and produce a 500-word report at "
            f"{artefacts}/report.md. Print the final report."
        )

    return result.final_output


def main() -> None:
    base = Path("/tmp/pyharness-pipeline-demo")
    if base.exists():
        shutil.rmtree(base)
    output = asyncio.run(run_pipeline(base, topic="quantum error correction"))
    print(output)


if __name__ == "__main__":
    main()
