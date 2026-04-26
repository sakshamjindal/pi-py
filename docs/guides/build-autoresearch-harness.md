# Building an Autoresearch Harness on pyharness

> **This is an example recipe.** To understand the kernel concepts this
> guide builds on — workspace, named agents, skills, extensions, the
> SDK API — read [`packages/coding-harness/README.md`](../../packages/coding-harness/README.md)
> **first**. This guide *applies* those concepts to a research domain;
> it does not re-derive them.

Same mental model as the finance harness: pyharness is the engine, the
autoresearch harness is a project directory with the right files.
**Pyharness doesn't know it's running research — it just runs whatever
AGENTS.md and tools you give it.**

The autoresearch harness automates the research process: literature review,
hypothesis formation, experiment execution, synthesis, and writing. Disk
is truth. Citations are mandatory. Time budgets prevent runaway costs.

---

## The directory structure

```
/research-project/
  README.md
  AGENTS.md                                    # research philosophy

  .pyharness/
    settings.json                              # defaults: model, time budget, citation style
    agents/
      literature-review.md                     # searches, reads, summarises papers
      synthesise.md                            # combines findings into coherent narrative
      experiment-runner.md                     # runs computational experiments
      critic.md                                # challenges claims, checks citations
    tools/
      __init__.py
      notes.py                                 # note_append, note_list, note_search
      pdf.py                                   # pdf_read, pdf_extract_figures
      cite.py                                  # cite_lookup, cite_format, cite_verify
      notebook.py                              # notebook_run, notebook_create
      data.py                                  # dataset_load, dataset_describe
    skills/
      pubmed-search/
        SKILL.md
        tools.py                               # pubmed_search, pubmed_fetch_abstract
      arxiv-fetch/
        SKILL.md
        tools.py                               # arxiv_search, arxiv_download
      statistical-tests/
        SKILL.md
        tools.py                               # run_ttest, run_anova, run_regression
    extensions/
      cite_audit.py                            # blocks synthesis writes without citations
      time_budget.py                           # wall-time cap from settings

  notes/                                       # disk-as-truth: all findings live here
    literature/                                # paper summaries and extracts
    hypotheses/                                # stated hypotheses with evidence
    experiments/                               # experiment logs and results
    synthesis/                                 # draft synthesis documents
    gaps/                                      # identified knowledge gaps

  data/                                        # datasets and processed data
    raw/
    processed/

  notebooks/                                   # computational notebooks
    exploratory/
    final/

  RESEARCH_PLAN.md                             # living research plan

  workflows/
    research_loop.py                           # iterative research workflow
    literature_sweep.py                        # broad literature search
    deep_read.py                               # focused reading of specific papers
```

---

## The AGENTS.md

```markdown
# /research-project/AGENTS.md

# Research Project

## Philosophy

- **Skepticism first.** Every claim needs evidence. Every finding needs
  a citation. If you can't cite it, you can't claim it.
- **Disk is truth.** All findings, hypotheses, and evidence live in files.
  The context window is working memory; disk is long-term memory. If you
  discovered something important, write it to notes/ before moving on.
- **Falsifiability.** Every hypothesis must state what would disprove it.
- **Incremental progress.** Each research session should leave the notes/
  directory in a better state than it found it.

## Citation standards

- Every factual claim must cite a specific source
- Citations use the format: [Author et al., Year, Section/Page]
- When summarising a paper, always include the DOI or arXiv ID
- Never paraphrase without attribution
- If two sources disagree, note the disagreement explicitly

## Failure modes to avoid

- **Citation laundering.** Don't cite a review paper when you mean the
  original study. Go to the primary source.
- **Confirmation bias.** Search for evidence against your hypothesis as
  hard as evidence for it.
- **Scope creep.** Stay focused on the research question. Note tangential
  findings in notes/gaps/ for later.
- **Premature synthesis.** Don't write the synthesis until you've done
  the reading.
```

---

## Agent definitions

```markdown
# /research-project/.pyharness/agents/literature-review.md

---
name: literature-review
description: Searches for, reads, and summarises academic papers
model: claude-opus-4-7
tools:
  - read
  - write
  - edit
  - grep
  - glob
  - web_search
  - web_fetch
  - note_append
  - note_list
  - note_search
  - pdf_read
  - cite_lookup
  - cite_verify
workdir: /research-project
---

# Literature Review Agent

You search for academic papers, read them, and produce structured summaries
in notes/literature/.

## Workflow

1. Read RESEARCH_PLAN.md to understand the research question
2. Read notes/literature/ to see what's already been covered
3. Search for relevant papers
4. For each relevant paper:
   a. Read the abstract and introduction
   b. If relevant, read methods and results
   c. Write a summary to notes/literature/{author-year-slug}.md
   d. Extract key claims with citations
   e. Note contradictions with existing findings
5. Update notes/gaps/ with identified knowledge gaps

## Skills available
- pubmed-search: For biomedical/life sciences literature
- arxiv-fetch: For physics, CS, math preprints
```

---

## Tools

```python
# /research-project/.pyharness/tools/notes.py
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from pyharness import Tool, ToolContext


class _NoteAppendArgs(BaseModel):
    path: str = Field(description="Relative path under notes/, e.g. 'literature/smith-2024.md'")
    content: str = Field(description="Content to append")


class _NoteAppendResult(BaseModel):
    path: str
    written: bool


class NoteAppendTool(Tool):
    name = "note_append"
    description = "Append content to a note file. Creates the file if it doesn't exist."
    args_schema = _NoteAppendArgs
    result_schema = _NoteAppendResult

    async def execute(self, args: _NoteAppendArgs, ctx: ToolContext) -> _NoteAppendResult:
        full_path = Path(ctx.workspace) / "notes" / args.path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        with full_path.open("a", encoding="utf-8") as fh:
            fh.write(args.content + "\n")
        return _NoteAppendResult(path=str(full_path), written=True)


class _NoteListArgs(BaseModel):
    directory: str = Field(default="", description="Subdirectory under notes/ to list")


class _NoteListResult(BaseModel):
    files: list[str]


class NoteListTool(Tool):
    name = "note_list"
    description = "List note files in a directory under notes/."
    args_schema = _NoteListArgs
    result_schema = _NoteListResult

    async def execute(self, args: _NoteListArgs, ctx: ToolContext) -> _NoteListResult:
        notes_dir = Path(ctx.workspace) / "notes" / args.directory
        if not notes_dir.is_dir():
            return _NoteListResult(files=[])
        files = sorted(str(f.relative_to(notes_dir)) for f in notes_dir.rglob("*.md"))
        return _NoteListResult(files=files)


TOOLS = [
    NoteAppendTool(),
    NoteListTool(),
]
```

---

## Extensions

```python
# /research-project/.pyharness/extensions/cite_audit.py
"""Blocks writes to notes/synthesis/ that don't contain citations."""

from pyharness import ExtensionAPI, HookOutcome


def register(api: ExtensionAPI) -> None:
    api.on("after_tool_call", _check_citations)


async def _check_citations(event, ctx):
    tool_name = event.payload.get("tool_name")
    if tool_name not in ("write", "edit", "note_append"):
        return HookOutcome.cont()

    args = event.payload.get("arguments") or {}
    path = args.get("file_path") or args.get("path") or ""
    if "synthesis" not in path:
        return HookOutcome.cont()

    content = args.get("content") or args.get("new_string") or ""
    if "[" not in content and "et al" not in content.lower():
        import sys
        sys.stderr.write(
            f"[cite_audit] WARNING: write to synthesis path {path} "
            f"contains no apparent citations\n"
        )

    return HookOutcome.cont()
```

```python
# /research-project/.pyharness/extensions/time_budget.py
"""Wall-time cap from settings.max_wall_seconds."""

import time
from pyharness import ExtensionAPI, HookOutcome

_DEFAULT_MAX_SECONDS = 3600


def register(api: ExtensionAPI) -> None:
    max_seconds = (
        api.settings.get("max_wall_seconds", _DEFAULT_MAX_SECONDS)
        if api.settings
        else _DEFAULT_MAX_SECONDS
    )
    state = {"start": 0.0}

    async def _on_start(event, ctx):
        state["start"] = time.monotonic()
        return HookOutcome.cont()

    async def _on_llm(event, ctx):
        elapsed = time.monotonic() - state["start"]
        if elapsed > max_seconds:
            return HookOutcome.deny(f"Time budget exceeded: {elapsed:.0f}s > {max_seconds}s")
        return HookOutcome.cont()

    api.on("session_start", _on_start)
    api.on("before_llm_call", _on_llm)
```

---

## Orchestration: the research loop

```python
# /research-project/workflows/research_loop.py
import argparse
import asyncio
from pathlib import Path

from coding_harness import CodingAgent, CodingAgentConfig

PROJECT = Path("/research-project")


async def run_agent(agent_name: str, prompt: str):
    config = CodingAgentConfig(workspace=PROJECT, agent_name=agent_name)
    agent = CodingAgent(config)
    return await agent.run(prompt)


async def new_research(question: str):
    plan_path = PROJECT / "RESEARCH_PLAN.md"
    plan_path.write_text(
        f"# Research Plan\n\n## Question\n{question}\n\n"
        f"## Status\nIn progress -- literature review phase.\n",
        encoding="utf-8",
    )
    await research_cycle()


async def research_cycle():
    # Step 1: Literature review
    await run_agent(
        "literature-review",
        "Search for papers relevant to the research question in RESEARCH_PLAN.md. "
        "Summarise each relevant paper in notes/literature/. "
        "Update notes/gaps/ with knowledge gaps."
    )

    # Step 2: Experiments (if hypotheses exist)
    hypotheses_dir = PROJECT / "notes" / "hypotheses"
    if hypotheses_dir.is_dir() and list(hypotheses_dir.glob("*.md")):
        await run_agent(
            "experiment-runner",
            "Check notes/hypotheses/ for testable hypotheses. "
            "Run experiments and record results in notes/experiments/."
        )

    # Step 3: Synthesis
    await run_agent(
        "synthesise",
        "Read all findings in notes/literature/ and notes/experiments/. "
        "Update the synthesis in notes/synthesis/. Every claim must have a citation."
    )


async def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--new", type=str, help="Start new research with this question")
    group.add_argument("--resume", action="store_true", help="Continue from existing state")
    args = parser.parse_args()

    if args.new:
        await new_research(args.new)
    else:
        await research_cycle()


if __name__ == "__main__":
    asyncio.run(main())
```

---

## What changes in pyharness for autoresearch

**Nothing.** Same answer as finance. You set up a project directory with
the right files in `.pyharness/` and run:

```bash
pyharness --workspace /research-project --agent literature-review "find papers on X"
```

Or drive it programmatically from `workflows/research_loop.py`.

---

## See also

- [`build-finance-harness.md`](build-finance-harness.md) — same
  pattern, different domain.
- [`packages/coding-harness/README.md`](../../packages/coding-harness/README.md)
  — the assembly layer you're using.
- [`packages/pyharness-sdk/README.md`](../../packages/pyharness-sdk/README.md)
  — kernel API for the tools and extensions you write.
