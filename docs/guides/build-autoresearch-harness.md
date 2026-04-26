# Building an autoresearch harness on `coding-harness`

This guide walks through building **`autoresearch-harness`** — a
domain-specific harness for long-horizon research tasks: read
sources, take notes, run experiments, write synthesis reports — by
**subclassing `coding_harness.CodingAgent`**.

Read [`build-finance-harness.md`](build-finance-harness.md) first;
this guide assumes you've seen the subclass pattern and only
elaborates the autoresearch-specific concerns. The defining trait of
autoresearch is that runs are **long** (often hundreds of turns),
**iterative** (notes accumulate; the agent revisits its own
outputs), and **structured** (a plan file is the shared truth
between turns). The features that matter most: context compaction,
session resume, the steering queue, and disk-as-truth.

---

## What's reused from `coding-harness`

Almost everything. Look at the table:

| Coding agent has | Autoresearch reuses / adds |
| --- | --- |
| AGENTS.md (project conventions) | reused — research project conventions go in `AGENTS.md` |
| Named sub-agents | adds `literature-review.md`, `synthesise.md`, `experiment-runner.md` at `<scope>/.pyharness/agents/` |
| Skills (market-data) | adds `pubmed-search`, `arxiv-fetch`, `notebook-execute` at `<scope>/.pyharness/skills/` |
| Built-in tools (read/write/edit/grep/glob/web_search/web_fetch) | **mostly reuses these as-is** plus adds `pdf_read`, `note_append`, `note_list`, `cite_lookup`, `notebook_run` |
| Settings hierarchy | reused — `AutoresearchSettings(Settings)` adds typed extras |
| Extension discovery | reused — citation-audit and time-budget extensions ship with autoresearch and are also user-extensible |
| `pyharness "task"` CLI | replaced by your own `autoresearch "task"` thin wrapper |

The big difference from finance: **autoresearch keeps several of the
coding built-ins** because reading files, writing files, grepping
notes, and fetching web pages are exactly what a researcher does.
You augment the registry rather than replace it.

---

## Step 1 — Project layout

```
autoresearch-harness/
  pyproject.toml
  src/autoresearch_harness/
    __init__.py
    cli.py            # `autoresearch` entry point
    runner.py         # AutoresearchHarness(CodingAgent) subclass
    config.py         # AutoresearchSettings — model, citation style, time budget
    extensions/
      time_budget.py  # cap total wall-time per run
      cite_audit.py   # block synthesis steps if claims lack citations
    tools/
      __init__.py     # research_registry() — coding builtins + research tools
      pdf.py          # pdf_read
      notes.py        # note_append, note_list — durable on-disk notes
      cite.py         # cite_lookup, bibtex_emit
      notebook.py     # notebook_run for code-bearing skills
  tests/
```

`pyproject.toml`:

```toml
[project]
name = "autoresearch-harness"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "pyharness",
  "coding-harness",
  "pypdf>=4.0",
  # search-provider SDK, jupyter client, etc.
]

[project.scripts]
autoresearch = "autoresearch_harness.cli:main"
```

---

## Step 2 — Notes and the plan file: disk-as-truth

The single most important design choice for an autoresearch agent
is: **make notes/plan files the agent's working memory, not its
context window.** Long runs blow up context; compaction helps but
lossy summaries hurt research quality. The fix is two new tools and
a system-prompt directive.

```python
# src/autoresearch_harness/tools/notes.py
import re
from datetime import datetime

from pydantic import BaseModel, Field
from pyharness import Tool, ToolContext, safe_path


class _AppendArgs(BaseModel):
    topic: str = Field(description="Short tag — becomes the filename slug.")
    content: str = Field(description="Markdown body to append.")


class NoteAppendTool(Tool):
    name = "note_append"
    description = (
        "Append a timestamped markdown note under notes/<topic>.md. "
        "Use this whenever you find something worth remembering across "
        "turns — citations, hypotheses, open questions, contradictions."
    )
    args_schema = _AppendArgs

    async def execute(self, args, ctx: ToolContext):
        notes_dir = safe_path(ctx.workspace, "notes")
        notes_dir.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-z0-9-]+", "-", args.topic.lower()).strip("-")
        path = notes_dir / f"{slug}.md"
        ts = datetime.utcnow().isoformat(timespec="seconds")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"\n## {ts}\n\n{args.content}\n")
        return {"ok": True, "path": str(path), "bytes": path.stat().st_size}


class _ListArgs(BaseModel):
    pass


class NoteListTool(Tool):
    name = "note_list"
    description = "List all notes by topic with their last-modified time."
    args_schema = _ListArgs

    async def execute(self, args, ctx: ToolContext):
        notes_dir = safe_path(ctx.workspace, "notes")
        if not notes_dir.is_dir():
            return []
        return sorted(
            ({"topic": p.stem, "mtime": p.stat().st_mtime}
             for p in notes_dir.glob("*.md")),
            key=lambda r: r["mtime"], reverse=True,
        )
```

In your registry, **augment** the coding built-ins rather than
replacing them — `read`/`write`/`grep`/`glob`/`web_search`/`web_fetch`
are exactly what you need to interrogate notes and read sources:

```python
# src/autoresearch_harness/tools/__init__.py
from coding_harness import builtin_registry

from .notes import NoteAppendTool, NoteListTool
from .pdf import PdfReadTool
from .cite import CiteLookupTool
from .notebook import NotebookRunTool


def research_registry():
    reg = builtin_registry()  # read/write/edit/bash/grep/glob/web_*
    reg.register(NoteAppendTool())
    reg.register(NoteListTool())
    reg.register(PdfReadTool())
    reg.register(CiteLookupTool())
    reg.register(NotebookRunTool())
    return reg
```

(For an autoresearch desk that should *not* execute shell, you'd
unregister `bash` here — that's a one-liner.)

---

## Step 3 — Citation audit + time budget extensions

Two cross-cutting concerns ship with the harness and are installed
in `_setup`:

```python
# src/autoresearch_harness/extensions/cite_audit.py
import re
from pyharness import ExtensionAPI, HookOutcome

_CITE_RE = re.compile(r"\[@[a-z0-9_-]+\]", re.I)


def install(api: ExtensionAPI) -> None:
    api.on("before_tool_call", _gate)


async def _gate(event, ctx):
    name = event.payload.get("tool_name")
    args = event.payload.get("arguments") or {}
    if name == "write" and args.get("path", "").endswith(("synthesis.md", "report.md")):
        content = args.get("content", "")
        headings = [h for h in content.splitlines() if h.startswith("#")]
        cites = _CITE_RE.findall(content)
        if headings and len(cites) < len(headings):
            return HookOutcome.deny(
                f"synthesis must cite — found {len(cites)} cite tags "
                f"for {len(headings)} sections. Use [@cite-key] format."
            )
    return HookOutcome.cont()
```

```python
# src/autoresearch_harness/extensions/time_budget.py
import time
from pyharness import ExtensionAPI, HookOutcome


def install(api: ExtensionAPI, max_wall_seconds: int) -> None:
    deadline = time.monotonic() + max_wall_seconds

    async def _gate(event, ctx):
        if time.monotonic() > deadline:
            return HookOutcome.deny(
                f"wall-time budget exhausted ({max_wall_seconds}s); "
                "end the run with a final summary based on disk notes."
            )
        return HookOutcome.cont()

    api.on("before_llm_call", _gate)
    api.on("before_tool_call", _gate)
```

---

## Step 4 — The harness class: ~30 lines

```python
# src/autoresearch_harness/runner.py
from pyharness import ExtensionAPI, ToolRegistry
from coding_harness import CodingAgent

from .config import AutoresearchSettings
from .extensions import cite_audit, time_budget
from .tools import research_registry


RESEARCH_PROMPT = """\
You are a long-horizon research agent. Your job is to investigate a
topic deeply, take rigorous notes, and produce a defensible
synthesis.

Disk is your memory. Your context window is working memory only.

- Use `note_append` after every meaningful finding. Tag it.
- Update `RESEARCH_PLAN.md` whenever the plan changes.
- Before starting a new line of inquiry, call `note_list` and
  `read RESEARCH_PLAN.md` to remind yourself what's known.
- Cite everything in synthesis output using `[@cite-key]` shorthand.
- When you hit a wall-time or budget limit, summarise from notes
  rather than starting new searches.

Tool usage:
- Prefer `web_search` for breadth, `web_fetch` for depth.
- For PDFs use `pdf_read`. For code-bearing analysis, `notebook_run`.
"""


class AutoresearchHarness(CodingAgent):
    BASE_SYSTEM_PROMPT = RESEARCH_PROMPT
    _settings_class = AutoresearchSettings

    def _default_tool_registry(self) -> ToolRegistry:
        return research_registry()

    def _tool_timeouts(self) -> dict[str, float]:
        # Inherit the coding defaults (bash/web_*) and add research-specific.
        tt = super()._tool_timeouts()
        tt.update({
            "pdf_read":     30.0,
            "notebook_run": 120.0,
        })
        return tt

    def _setup(self) -> None:
        super()._setup()
        api = ExtensionAPI(
            bus=self.event_bus,
            registry=self.tool_registry,
            settings=self.settings,
        )
        cite_audit.install(api)
        time_budget.install(api, max_wall_seconds=self.settings.max_wall_seconds)
```

`AutoresearchSettings` is a 5-line subclass of `Settings` adding
`max_wall_seconds`, `citation_style`, `compaction_threshold_pct=0.6`
(more aggressive than the coding default of 0.8), etc.

---

## Step 5 — CLI: resume by default

Long runs get interrupted. Make the CLI default to **continuing**
the most recent session in cwd, with `--new` to opt out:

```python
# src/autoresearch_harness/cli.py
import argparse, asyncio, sys
from pathlib import Path

from pyharness import Session
from coding_harness import CodingAgentConfig
from .runner import AutoresearchHarness


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="autoresearch")
    p.add_argument("prompt", nargs="*")
    p.add_argument("--workspace", type=Path, default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--agent", default=None)
    p.add_argument("--new", action="store_true",
                   help="Start a new session even if one exists in cwd.")
    p.add_argument("--max-wall-seconds", type=int, default=None)
    args = p.parse_args(argv)

    prompt = " ".join(args.prompt).strip()
    if not prompt:
        sys.stderr.write("error: no prompt provided.\n")
        return 2

    workspace = (args.workspace or Path.cwd()).resolve()

    resume_from = None
    if not args.new:
        recent = Session.list_recent(workspace, n=1)
        if recent:
            resume_from = recent[0].session_id

    cli_overrides = {}
    if args.max_wall_seconds is not None:
        cli_overrides["max_wall_seconds"] = args.max_wall_seconds

    cfg = CodingAgentConfig(
        workspace=workspace,
        model=args.model,
        agent_name=args.agent,
        resume_from=resume_from,
        cli_overrides=cli_overrides,
    )
    result = asyncio.run(AutoresearchHarness(cfg).run(prompt))
    sys.stdout.write(result.final_output.rstrip() + "\n")
    return 0 if result.completed else 1
```

---

## Step 6 — Multi-agent through subprocesses

Pi-py refuses in-loop sub-agent delegation by design (see
[`DESIGN.md`](../../DESIGN.md)). For autoresearch, spawn specialist
sub-agents as **subprocesses** that share the same disk-truth (the
`notes/` directory and `RESEARCH_PLAN.md`):

```bash
# Inside the orchestrator's prompt or as a tool the orchestrator calls:
$ autoresearch --agent literature-review "find prior work on Y"
$ autoresearch --agent experiment-runner "test hypothesis Z"
$ autoresearch --agent synthesise   "produce report.md from notes/ and RESEARCH_PLAN.md"
```

Each subprocess gets its own session log, context window, and retry
budget. They communicate via files. The orchestrator reads their
session logs (or just their final outputs) to incorporate results.

The `--agent` flag is provided for free by `coding-harness` — it
loads `<scope>/.pyharness/agents/<name>.md` and uses that
frontmatter to choose model + tool subset + system prompt body.

---

## What you get for free from `coding-harness`

For autoresearch specifically, what you didn't have to write:

- **Session resume / fork** (pyharness-sdk) wired through
  `CodingAgentConfig.resume_from` / `fork_from` / `fork_at_event`.
- **Transparent compaction** with a tunable threshold via
  `Settings.compaction_threshold_pct`.
- **Steering queue** via `agent.start()` + `handle.steer(...)`.
- **JSONL session log**, replayable.
- **Extension discovery** at `<scope>/.pyharness/extensions/` so
  *users* of your harness can add their own audit / dedup / kill
  switches without touching your code.
- **Named sub-agent loading** for the multi-agent subprocess
  pattern (Step 6).
- **AGENTS.md walking** — research project conventions get included
  in the system prompt automatically.
- **Skills loader** — heavyweight skills like `notebook-execute`
  load on demand rather than padding the system prompt.
- **The whole `pyharness.Agent` loop**.

What you wrote: research-specific tools, citation extension, time
budget extension, settings extras, system prompt, ~30-line subclass,
~30-line CLI. Everything else is inherited.

---

## See also

- [`build-finance-harness.md`](build-finance-harness.md) — same
  pattern, different domain. Read alongside this for the recipe.
- [`packages/coding-harness/README.md`](../../packages/coding-harness/README.md)
  — what you're inheriting; especially the `CodingAgent.__init__`
  walkthrough.
- [`packages/pyharness-sdk/README.md`](../../packages/pyharness-sdk/README.md)
  — kernel API for the tools and extensions you write.
