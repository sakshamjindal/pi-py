# Building an autoresearch harness on pyharness-sdk

This guide walks through building **`autoresearch-harness`** — a
domain-specific harness that runs long-horizon research tasks
autonomously: read sources, take notes, run experiments, write
synthesis reports. It depends only on `pyharness-sdk` (the kernel).

The shape mirrors what `coding-harness` does for software
engineering and what
[`build-finance-harness.md`](build-finance-harness.md) sketches for
trading. Read those alongside this for the cross-domain pattern.

---

## What you're building

| Coding agent has | Autoresearch harness has |
| --- | --- |
| AGENTS.md (project conventions) | `RESEARCH_PLAN.md` (current plan + open questions) |
| Named sub-agents (research-analyst.md) | Named sub-agents (`literature-review.md`, `synthesise.md`, `experiment-runner.md`) |
| Skills (market-data) | Skills (`pubmed-search`, `arxiv-fetch`, `notebook-execute`) |
| Built-in tools (read/write/edit/bash) | Domain tools (`web_search`, `web_fetch`, `pdf_read`, `notebook_run`, `note_append`, `cite_lookup`) |
| `~/.pyharness/` | `~/.autoresearch/` |
| `pyharness "fix the failing tests"` | `autoresearch "investigate prior work on X and write a 2-page synthesis"` |

The defining trait of autoresearch is that runs are **long** (often
hundreds of turns), **iterative** (notes accumulate; the agent
revisits its own outputs), and **structured** (a plan file is the
shared truth between turns). The kernel features that matter most:
context compaction, session resume, the steering queue, and disk-as-truth.

---

## Step 1 — Project layout

```
autoresearch-harness/
  pyproject.toml
  src/autoresearch_harness/
    __init__.py
    cli.py            # `autoresearch` entry point
    runner.py         # ResearchAgent assembly class
    config.py         # Settings: model, search providers, citation style, time budget
    workspace.py      # Walks RESEARCH_PLAN.md, plan_root discovery
    plan.py           # Plan-file parser + agent-side helpers
    tools/
      __init__.py
      search.py       # web_search wrapping the configured provider
      fetch.py        # web_fetch + PDF / HTML extraction
      notes.py        # note_append, note_list — durable on-disk notes
      cite.py         # cite_lookup, bibtex_emit
      notebook.py     # notebook_run for code-bearing skills
    extensions/
      time_budget.py  # cap total run wall-time + turns from settings
      cite_audit.py   # block synthesis steps if claims lack citations
  tests/
```

`pyproject.toml`:

```toml
[project]
name = "autoresearch-harness"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "pyharness",          # the SDK kernel
  "pydantic>=2.6",
  "httpx>=0.27",        # for fetch
  "pypdf>=4.0",         # for pdf_read
  # search-provider SDK, jupyter client, etc.
]

[project.scripts]
autoresearch = "autoresearch_harness.cli:main"
```

---

## Step 2 — Notes and the plan file: disk-as-truth

The single most important design choice for an autoresearch agent is
**make the notes/plan files the agent's working memory, not its
context window**. Long runs blow up context windows; compaction
helps but lossy summaries hurt research quality. The fix:

- A `notes/` directory the agent appends to via a `note_append` tool.
- A `RESEARCH_PLAN.md` file the agent rewrites as the plan evolves.
- Both injected into the system prompt at every turn (or read on
  demand via `read`).

```python
# src/autoresearch_harness/tools/notes.py
from datetime import datetime
from pathlib import Path
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
        path = notes_dir / f"{_slugify(args.topic)}.md"
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
            ({"topic": p.stem, "mtime": p.stat().st_mtime} for p in notes_dir.glob("*.md")),
            key=lambda r: r["mtime"], reverse=True,
        )
```

The system prompt should mention these tools explicitly: *"Use
`note_append` aggressively. Anything you'd want available three
turns from now must be on disk."*

---

## Step 3 — Citation discipline as an extension

For research output to be trustworthy, claims need citations. Make
that a hard rule via an extension on `before_tool_call`:

```python
# src/autoresearch_harness/extensions/cite_audit.py
import re
from pyharness import ExtensionAPI, HookOutcome

_CITE_RE = re.compile(r"\[@[a-z0-9_-]+\]", re.I)


def install(api: ExtensionAPI, settings) -> None:
    api.on("before_tool_call", _gate)


async def _gate(event, ctx):
    name = event.payload.get("tool_name")
    args = event.payload.get("arguments") or {}

    # Only audit calls that produce final-ish output.
    if name == "write" and args.get("path", "").endswith(("synthesis.md", "report.md")):
        content = args.get("content", "")
        # Crude check: at least one [@cite-key] reference per heading.
        headings = [h for h in content.splitlines() if h.startswith("#")]
        cites = _CITE_RE.findall(content)
        if headings and len(cites) < len(headings):
            return HookOutcome.deny(
                f"synthesis must cite — found {len(cites)} cite tags "
                f"for {len(headings)} sections. Use [@cite-key] format."
            )
    return HookOutcome.cont()
```

The denial reason flows back to the LLM as a tool result; the agent
sees what's wrong and corrects rather than the run crashing.

---

## Step 4 — Time budget extension

Long runs need a hard ceiling. Cap total wall-time, not just
`max_turns`:

```python
# src/autoresearch_harness/extensions/time_budget.py
import time
from pyharness import ExtensionAPI, HookOutcome


def install(api: ExtensionAPI, settings) -> None:
    started = time.monotonic()
    deadline = started + settings.max_wall_seconds

    async def _gate(event, ctx):
        if time.monotonic() > deadline:
            return HookOutcome.deny(
                f"wall-time budget exhausted "
                f"({settings.max_wall_seconds}s); end the run with a "
                f"final summary based on what's already on disk."
            )
        return HookOutcome.cont()

    api.on("before_llm_call", _gate)
    api.on("before_tool_call", _gate)
```

The agent sees `denied: wall-time budget exhausted` and writes a
synthesis from disk-truth instead of starting new investigations.

---

## Step 5 — The system prompt that makes this work

The kernel doesn't know about research conventions. The prompt is
where you make them concrete:

```python
BASE_SYSTEM_PROMPT = """\
You are a long-horizon research agent. Your job is to investigate a
topic deeply, take rigorous notes, and produce a defensible
synthesis.

Disk is your memory. Your context window is working memory only.

- Use `note_append` after every meaningful finding. Tag it.
- Update `RESEARCH_PLAN.md` whenever the plan changes — never let
  it go stale.
- Before starting any new line of inquiry, call `note_list` and
  `read RESEARCH_PLAN.md` to remind yourself what's known.
- Cite everything in synthesis output using `[@cite-key]` shorthand.
- When you hit a wall-time or budget limit, summarise from notes
  rather than starting new searches.

Tool usage:
- Prefer `web_search` for breadth, `web_fetch` for depth.
- For PDFs use `pdf_read`. For code-bearing analysis, `notebook_run`
  in the configured kernel.
"""
```

Inject the current `RESEARCH_PLAN.md` content into the prompt at
each turn boundary via a `before_llm_call` extension if you want
the plan to always be top-of-mind, OR rely on the agent to `read`
it when needed (cheaper, requires more discipline).

---

## Step 6 — Assembly + CLI + resume

The assembly is the same shape as
[`build-finance-harness.md`](build-finance-harness.md) Step 6 — read
settings, walk the workspace, build the registry, install
extensions, instantiate `pyharness.Agent`. Two specifics for
autoresearch:

**Resume is a feature, not a corner case.** Long runs get
interrupted. Make the CLI default to *continuing* the most recent
session in the cwd, with `--new` to opt out:

```python
def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="autoresearch")
    p.add_argument("prompt", nargs="*")
    p.add_argument("--workspace", type=Path, default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--new", action="store_true",
                   help="Start a new session even if one exists in cwd.")
    p.add_argument("--max-wall-seconds", type=int, default=None,
                   help="Override the time-budget extension.")
    args = p.parse_args(argv)

    workspace = (args.workspace or Path.cwd()).resolve()

    if not args.new:
        recent = Session.list_recent(workspace, n=1)
        if recent:
            cfg.resume_from = recent[0].session_id

    # ... rest as in build-finance-harness Step 7
```

**Compaction tuning.** Lower `compaction_threshold_pct` so that
long runs compact earlier and more aggressively (the disk notes are
the durable record; the in-memory transcript is disposable):

```python
options = AgentOptions(
    model=self.model,
    max_turns=self.settings.max_turns,
    model_context_window=self.settings.model_context_window,
    compaction_threshold_pct=0.6,    # compact at 60% rather than 80%
    settings_snapshot=self.settings.model_dump(),
)
```

---

## Step 7 — Multi-agent through subprocesses

Pi-py refuses in-loop sub-agent delegation by design (see
[`DESIGN.md`](../../DESIGN.md)). But long research often benefits
from specialist sub-agents — a literature-review agent, a
synthesis agent, an experiment-runner agent.

The pi-py answer: spawn them as **subprocesses** that share the
same disk-truth (the `notes/` and `RESEARCH_PLAN.md` files):

```bash
# Inside the system prompt or as a tool the orchestrator calls:
$ autoresearch --agent literature-review "find prior work on Y"
$ autoresearch --agent experiment-runner "test hypothesis Z"
$ autoresearch --agent synthesise "produce report.md from notes/ and RESEARCH_PLAN.md"
```

Each subprocess gets its own session log, its own context window,
its own retry budget. They communicate via files. The orchestrator
agent reads their session logs (or just their final outputs) to
incorporate results.

---

## What you get for free from `pyharness-sdk`

For an autoresearch harness specifically:

- **Session resume / fork by event sequence.** Mid-run interruption
  isn't fatal — the agent picks up where it left off. Forking lets
  you branch from a known-good state to explore alternatives.
- **Transparent compaction.** The middle of a long transcript gets
  summarised by the cheaper `summarization_model` while the system
  prompt and recent turns stay verbatim. Critical for hundred-turn
  runs.
- **Steering queue.** `handle.steer("also check Y")` is consumed at
  the next turn boundary — you can intervene from a parent process
  or a UI without restarting.
- **Pydantic-validated tools that don't crash.** A bad `pdf_read`
  call returns `ok=False` with the error; the agent retries or
  moves on rather than the run dying.
- **Append-only JSONL log.** Every search query, every URL fetched,
  every note appended is recorded. Auditability is built-in.
- **Event bus for cross-cutting concerns.** Citation auditing,
  budget enforcement, dedup of redundant searches — all extensions
  rather than tool changes.

---

## See also

- [`build-finance-harness.md`](build-finance-harness.md) — same
  recipe, different domain. Read both for the cross-domain pattern.
- [`packages/pyharness-sdk/README.md`](../../packages/pyharness-sdk/README.md)
  — kernel API and the loop diagram.
- [`packages/coding-harness/README.md`](../../packages/coding-harness/README.md)
  — the worked example with full source you can read alongside this
  guide.
