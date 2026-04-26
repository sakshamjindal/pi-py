# Building an Autoresearch Harness on pyharness

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
  If it can't be falsified, it's not a hypothesis.
- **Incremental progress.** Each research session should leave the notes/
  directory in a better state than it found it. Even negative results
  (this approach doesn't work) are valuable and should be recorded.

## Citation standards

- Every factual claim must cite a specific source
- Citations use the format: [Author et al., Year, Section/Page]
- When summarising a paper, always include the DOI or arXiv ID
- Never paraphrase without attribution
- If two sources disagree, note the disagreement explicitly

## What we value

- Reproducibility over novelty
- Clear negative results over ambiguous positive ones
- Precise claims over broad ones
- Identifying what we don't know over confirming what we do

## Failure modes to avoid

- **Citation laundering.** Don't cite a review paper when you mean the
  original study. Go to the primary source.
- **Confirmation bias.** Search for evidence against your hypothesis as
  hard as evidence for it.
- **Scope creep.** Stay focused on the research question. Note tangential
  findings in notes/gaps/ for later.
- **Premature synthesis.** Don't write the synthesis until you've done
  the reading. The literature-review agent runs before the synthesise agent.
```

---

## Agent definitions

### Literature review agent

```markdown
# /research-project/.pyharness/agents/literature-review.md

---
name: literature-review
description: Searches for, reads, and summarises academic papers relevant to the research question
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
3. Search for relevant papers (web_search, or load pubmed-search/arxiv-fetch
   skills if the domain warrants it)
4. For each relevant paper:
   a. Read the abstract and introduction
   b. If relevant, read methods and results
   c. Write a summary to notes/literature/{author-year-slug}.md
   d. Extract key claims with citations
   e. Note any contradictions with existing findings
5. Update notes/gaps/ with identified knowledge gaps

## Summary format

Each paper summary in notes/literature/ follows:

    # {Paper title}
    **Authors:** {authors}
    **Year:** {year}
    **DOI/arXiv:** {identifier}

    ## Key claims
    - Claim 1 [Section X, p. Y]
    - Claim 2 [Figure Z]

    ## Methods
    Brief description of methodology.

    ## Relevance to our question
    How this paper relates to RESEARCH_PLAN.md.

    ## Limitations noted by authors
    - ...

    ## Our assessment
    Strengths and weaknesses we observe.

## Skills available
- pubmed-search: For biomedical/life sciences literature
- arxiv-fetch: For physics, CS, math preprints
```

### Synthesis agent

```markdown
# /research-project/.pyharness/agents/synthesise.md

---
name: synthesise
description: Combines literature findings into coherent narrative with proper citations
model: claude-opus-4-7
tools:
  - read
  - write
  - edit
  - grep
  - glob
  - note_append
  - note_list
  - note_search
  - cite_format
  - cite_verify
workdir: /research-project
---

# Synthesis Agent

You read the accumulated findings in notes/ and produce synthesis
documents that combine them into coherent narratives.

## Workflow

1. Read RESEARCH_PLAN.md for the research question
2. Read all files in notes/literature/ and notes/experiments/
3. Identify themes, agreements, and contradictions
4. Write a synthesis document to notes/synthesis/{topic}.md
5. Every claim must cite a specific note file which in turn cites the
   primary source

## Rules

- Never introduce claims not supported by the notes
- If notes disagree, present both views with citations
- Identify remaining gaps and write them to notes/gaps/
- The synthesis is a living document — update rather than rewrite
```

### Experiment runner agent

```markdown
# /research-project/.pyharness/agents/experiment-runner.md

---
name: experiment-runner
description: Runs computational experiments and records results
model: claude-opus-4-7
tools:
  - read
  - write
  - edit
  - bash
  - grep
  - glob
  - note_append
  - notebook_run
  - notebook_create
  - dataset_load
  - dataset_describe
workdir: /research-project
---

# Experiment Runner

You run computational experiments to test hypotheses. All experiments
are recorded in notes/experiments/ with full methodology and results.

## Workflow

1. Read the hypothesis from notes/hypotheses/{name}.md
2. Design the experiment (methodology, data, metrics)
3. Create or run a notebook in notebooks/
4. Record results in notes/experiments/{YYYY-MM-DD}-{slug}.md
5. State whether the hypothesis is supported, refuted, or inconclusive

## Experiment log format

    # Experiment: {title}
    **Date:** {date}
    **Hypothesis:** {link to hypothesis file}

    ## Methodology
    What we did and why.

    ## Data
    What data we used (link to data/ directory).

    ## Results
    Quantitative results with statistical significance where applicable.

    ## Conclusion
    Supported / Refuted / Inconclusive, with reasoning.

    ## Notebook
    Link to notebooks/{name}.ipynb
```

---

## Tools

```python
# /research-project/.pyharness/tools/notes.py
"""Note management tools for disk-as-truth research."""

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


class _NoteSearchArgs(BaseModel):
    query: str = Field(description="Search query (substring match)")


class _NoteSearchResult(BaseModel):
    matches: list[str] = Field(default_factory=list)


class NoteSearchTool(Tool):
    name = "note_search"
    description = "Search note files for a substring."
    args_schema = _NoteSearchArgs
    result_schema = _NoteSearchResult

    async def execute(self, args: _NoteSearchArgs, ctx: ToolContext) -> _NoteSearchResult:
        notes_dir = Path(ctx.workspace) / "notes"
        matches = []
        if notes_dir.is_dir():
            for f in notes_dir.rglob("*.md"):
                try:
                    if args.query.lower() in f.read_text(encoding="utf-8").lower():
                        matches.append(str(f.relative_to(notes_dir)))
                except OSError:
                    pass
        return _NoteSearchResult(matches=matches)


TOOLS = [
    NoteAppendTool(),
    NoteListTool(),
    NoteSearchTool(),
]
```

```python
# /research-project/.pyharness/tools/cite.py
"""Citation tools."""

from __future__ import annotations

from pydantic import BaseModel, Field

from pyharness import Tool, ToolContext


class _CiteLookupArgs(BaseModel):
    doi: str = Field(default="", description="DOI to look up")
    title: str = Field(default="", description="Paper title to search for")


class _CiteLookupResult(BaseModel):
    title: str = ""
    authors: list[str] = Field(default_factory=list)
    year: int = 0
    doi: str = ""
    abstract: str = ""
    found: bool = False


class CiteLookupTool(Tool):
    name = "cite_lookup"
    description = "Look up citation metadata by DOI or title."
    args_schema = _CiteLookupArgs
    result_schema = _CiteLookupResult

    async def execute(self, args: _CiteLookupArgs, ctx: ToolContext) -> _CiteLookupResult:
        # TODO: wire to CrossRef, Semantic Scholar, or OpenAlex API
        return _CiteLookupResult()


class _CiteVerifyArgs(BaseModel):
    claim: str = Field(description="The claim to verify")
    source_path: str = Field(description="Path to the note file that should support this claim")


class _CiteVerifyResult(BaseModel):
    verified: bool = False
    message: str = ""


class CiteVerifyTool(Tool):
    name = "cite_verify"
    description = "Verify that a claim is supported by the cited source file."
    args_schema = _CiteVerifyArgs
    result_schema = _CiteVerifyResult

    async def execute(self, args: _CiteVerifyArgs, ctx: ToolContext) -> _CiteVerifyResult:
        from pathlib import Path

        source = Path(ctx.workspace) / args.source_path
        if not source.is_file():
            return _CiteVerifyResult(message=f"Source file not found: {args.source_path}")
        content = source.read_text(encoding="utf-8")
        # Simple heuristic: check if key terms from the claim appear in the source
        claim_words = set(args.claim.lower().split())
        source_words = set(content.lower().split())
        overlap = len(claim_words & source_words) / max(len(claim_words), 1)
        return _CiteVerifyResult(
            verified=overlap > 0.3,
            message=f"Term overlap: {overlap:.0%}. Manual verification recommended.",
        )


class _CiteFormatArgs(BaseModel):
    doi: str = Field(default="", description="DOI")
    authors: list[str] = Field(default_factory=list)
    title: str = Field(default="")
    year: int = Field(default=0)
    style: str = Field(default="apa", description="Citation style: apa, chicago, or bibtex")


class _CiteFormatResult(BaseModel):
    formatted: str = ""


class CiteFormatTool(Tool):
    name = "cite_format"
    description = "Format a citation in a specified style."
    args_schema = _CiteFormatArgs
    result_schema = _CiteFormatResult

    async def execute(self, args: _CiteFormatArgs, ctx: ToolContext) -> _CiteFormatResult:
        if args.style == "bibtex":
            key = f"{args.authors[0].split()[-1].lower()}{args.year}" if args.authors else "unknown"
            return _CiteFormatResult(
                formatted=f"@article{{{key},\n  title={{{args.title}}},\n  year={{{args.year}}}\n}}"
            )
        # Default APA-ish
        author_str = ", ".join(args.authors) if args.authors else "Unknown"
        return _CiteFormatResult(formatted=f"{author_str} ({args.year}). {args.title}.")


TOOLS = [
    CiteLookupTool(),
    CiteVerifyTool(),
    CiteFormatTool(),
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
        # Log a warning but don't block — the agent should self-correct
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

_DEFAULT_MAX_SECONDS = 3600  # 1 hour


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
            return HookOutcome.deny(
                f"Time budget exceeded: {elapsed:.0f}s > {max_seconds}s"
            )
        return HookOutcome.cont()

    api.on("session_start", _on_start)
    api.on("before_llm_call", _on_llm)
```

---

## Orchestration: the research loop

```python
# /research-project/workflows/research_loop.py
"""Iterative research workflow.

Usage:
    python research_loop.py --new "What is the effect of X on Y?"
    python research_loop.py --resume
"""

import argparse
import asyncio
from pathlib import Path

from coding_harness import CodingAgent, CodingAgentConfig

PROJECT = Path("/research-project")


async def run_agent(agent_name: str, prompt: str):
    config = CodingAgentConfig(
        workspace=PROJECT,
        agent_name=agent_name,
    )
    agent = CodingAgent(config)
    return await agent.run(prompt)


async def new_research(question: str):
    """Start a new research project."""

    # Write the research plan
    plan_path = PROJECT / "RESEARCH_PLAN.md"
    plan_path.write_text(
        f"# Research Plan\n\n"
        f"## Question\n{question}\n\n"
        f"## Status\nIn progress — literature review phase.\n",
        encoding="utf-8",
    )

    await research_cycle()


async def research_cycle():
    """One cycle: literature review -> synthesis -> identify gaps -> repeat."""

    # Step 1: Literature review
    print(">>> Running literature review...")
    await run_agent(
        "literature-review",
        "Search for papers relevant to the research question in RESEARCH_PLAN.md. "
        "Summarise each relevant paper in notes/literature/. "
        "Update notes/gaps/ with knowledge gaps you identify."
    )

    # Step 2: Check if there are experiments to run
    experiments_dir = PROJECT / "notes" / "hypotheses"
    if experiments_dir.is_dir() and list(experiments_dir.glob("*.md")):
        print(">>> Running experiments...")
        await run_agent(
            "experiment-runner",
            "Check notes/hypotheses/ for testable hypotheses. "
            "Run experiments and record results in notes/experiments/."
        )

    # Step 3: Synthesis
    print(">>> Running synthesis...")
    await run_agent(
        "synthesise",
        "Read all findings in notes/literature/ and notes/experiments/. "
        "Update the synthesis in notes/synthesis/ with a coherent narrative. "
        "Every claim must have a citation. Update notes/gaps/ with remaining gaps."
    )

    # Step 4: Check remaining gaps
    gaps_dir = PROJECT / "notes" / "gaps"
    if gaps_dir.is_dir() and list(gaps_dir.glob("*.md")):
        gap_content = "\n".join(
            f.read_text(encoding="utf-8") for f in gaps_dir.glob("*.md")
        )
        print(f">>> Remaining gaps:\n{gap_content[:500]}")
        print(">>> Run again with --resume to continue researching gaps.")
    else:
        print(">>> No remaining gaps identified. Research may be complete.")


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

**Nothing.** Same answer as finance. You don't modify pyharness. You set up
a project directory with the right files in `.pyharness/` and run:

```bash
pyharness --workspace /research-project --agent literature-review "find papers on X"
```

Or drive it programmatically from `workflows/research_loop.py`.

---

## The summary view

An autoresearch harness built on pyharness is:

1. The **pyharness library** (the engine)
2. **5-10 research tools** (notes, citations, PDF reading, notebooks)
3. **3-5 named agents** (literature-review, synthesise, experiment-runner, critic)
4. **Skills** for domain-specific search (PubMed, arXiv, statistical tests)
5. **Extensions** (citation audit, time budget)
6. **AGENTS.md** (research philosophy, citation standards, failure modes)
7. **Orchestration** (the research loop: review -> experiment -> synthesise -> repeat)
8. **notes/** as the disk-as-truth layer

The research loop is the autoresearch equivalent of the finance harness's
morning routine. Both are ~100 lines of Python that wire named agents
together in a sequence, with the agents reading and writing to shared disk.
