# pi-py design

> **Two-part document, one source of truth.**
>
> - **Part 1 — Philosophy** is the design choices: principles,
>   refusals, comparable projects, what we borrowed. Read this if
>   you're skimming or auditing intent.
> - **Part 2 — Operational reference** is the full user manual:
>   workspace model, AGENTS.md, settings, named agents, skills,
>   extensions, plugins, the SDK API, the CLI, sessions. **Mirrored
>   from [`packages/coding-harness/README.md`](packages/coding-harness/README.md)**
>   so an agent reading this single file gets everything it needs to
>   understand or modify the codebase. If they ever drift, the
>   package README is the source of truth — keep them in sync.

---

# Part 1 — Philosophy

This part records the design principles and explicit refusals.
Treat the refusals list as the answer key for "should we add X?":
if X is on the list, the answer is "as an extension, not in core."

> **Comparable projects:** [pi-mono](https://github.com/badlogic/pi-mono)
> (TypeScript) — the runtime-semantics inspiration; [Claude
> Code](https://docs.claude.com/en/docs/claude-code) — the file-format
> inspiration (frontmatter agents, SKILL.md); the
> [agentskills.io](https://agentskills.io/) standard — the SKILL.md
> spec we adopt; [AGENTS.md](https://agents.md/) — the cross-vendor
> guidance file pyharness reads from every directory between the
> project root and the workspace.

## Design principles

1. **Headless-first.** Programmatic invocation (CLI + SDK) is canonical.
   No TUI, no interactive permission prompts, no chat-product features.
2. **Minimalism for the runtime, practical for capability.** Smaller
   core than Claude Code, slightly larger defaults than pi.
3. **Multi-vendor LLM, single-language harness.** LiteLLM handles
   providers; Python is the harness language because tools live in
   Python.
4. **Files-as-truth.** The session log on disk (JSONL) is the durable
   layer. The context window is working memory.
5. **Two ways to be an agent: place-based and name-based.** Both are
   first-class. Workspace is decoupled from identity.
6. **Always-on tools vs conditional skills.** Builtins are always
   registered. Routine non-builtin tools are pinned in agent frontmatter
   `tools:` (additive over builtins). Skills are reserved for genuinely
   conditional capabilities loaded on demand via `load_skill`.
7. **Extensions are opt-in, never auto-loaded.** Discovery surfaces a
   catalog (filesystem + Python entry points). Activation requires an
   explicit name in frontmatter `extensions:`, programmatic
   `extensions_enabled`, or a CLI flag. Filesystem presence alone never
   triggers `register(api)`.
8. **Multi-agent through process spawning.** No in-loop sub-agent
   delegation. Sub-agents are subprocess invocations.
9. **One operating directory; project boundary is required and
   explicit.** The user supplies `workspace`. Pyharness walks up
   from it to find a `.pyharness/` marker, which becomes the
   project root (a derived value, not a separate input). The walk
   stops at `$HOME`. **No marker anywhere above the workspace = hard
   error**, not a silent fallback to personal-only config — this
   prevents home-directory config from leaking into unrelated
   sessions (Claude Code's well-known failure mode). Two config
   scopes: personal (`~/.pyharness/`) and project (the discovered
   marker). AGENTS.md is read from `~/AGENTS.md` plus every
   directory between project root and workspace inclusive — bounded
   so guidance from outside the project tree never applies.
   `pyharness init` creates the marker; `--bare` skips the
   requirement entirely.
10. **Plugin ecosystem via Python entry points.** Pip-installed
    libraries publish skills (`pyharness.skills`) and extensions
    (`pyharness.extensions`) without writing into `~/.pyharness/`.
    Namespaced as `<package>:<name>`. No bespoke plugin manifest, no
    install command.
11. **Standard formats from Claude Code; semantics from pi.** Frontmatter
    format is Claude Code-compatible (incl. SKILL.md and `<system-reminder>`
    skill index injection); runtime semantics follow pi.
12. **Build the minimum that ships; defer features until concrete need
    appears.**

## Explicit refusals

These features are rejected by design. If a future need genuinely
requires one, it lands as an extension, not as core code.

- **TUI in the SDK or coding-harness package.** The agent loop must
  remain programmatic; its behaviour cannot depend on terminal state.
  A TUI is allowed only as a separate package (`packages/tui/`) that
  subscribes to the event bus and renders — never threading back into
  the SDK or coding-harness layers.
- **Interactive permission prompts.** Tools execute or fail. Approval
  gates would block scheduled and SDK-driven runs.
- **In-loop sub-agent delegation** (Task tool, delegate tool). Multi-
  agent runs are subprocesses; the harness composes from the outside.
- **Plan mode.** Plans are a UX concept that hides work from the
  observability layer. The agent already structures its work via tool
  calls.
- **TodoWrite tool.** The agent manages plans by writing files like any
  other artefact.
- **MultiEdit tool.** Single Edit only — keeps the diff surface
  reviewable and the failure modes few.
- **Approval gates.** No human-in-the-loop pauses inside the loop.
- **Doom-loop detection.** `max_turns` is enough. Heuristics that guess
  "stuck" are noise.
- **Cost budget enforcement.** Logging is enough for v1; if budgets
  matter later, an extension subscribing to `after_llm_call` is the
  right place.
- **Sandbox provider abstraction.** Direct execution + a small list of
  hard-blocks for v1. Defer the abstraction until there is concrete
  evidence we need to swap implementations.
- **Permission modes** (`acceptEdits`, `bypassPermissions`). There is
  one mode: tools run.
- **Custom slash commands.** We are headless; there is no command line.
- **MCP support.** Out of scope for v1. The tool ABC is local Python;
  MCP can ship as an extension later.
- **Plugin trust / sandboxing.** Entry-point plugins run arbitrary
  Python at import time. We rely on pip's install boundary; we do not
  gate plugins behind an allow list or signature check in v1.
- **Mid-run extension toggle.** Extensions are bound at session start
  only. Disabling mid-run would leave dangling tool references and
  partial-effect state. If a kill switch is needed, ship it as an
  extension that gates other extensions internally.

## Architecture

Three packages — a small kernel (`packages/pyharness-sdk`), one
concrete application built on it (`packages/coding-harness`), and a
stdlib REPL (`packages/tui`). The kernel is loop-only; conventions
(file discovery, settings, named agents, skills, extensions, tools,
CLI) live in the application. Domain-specific harnesses (finance,
autoresearch, …) are project directories with `.pyharness/` files —
no subclassing, no fork. Part 2 below has the full operational
walkthrough; the in-tree code is in
[`packages/pyharness-sdk/src/pyharness/`](packages/pyharness-sdk/src/pyharness/)
and
[`packages/coding-harness/src/coding_harness/`](packages/coding-harness/src/coding_harness/).

## What we borrowed

- **Frontmatter format** — Claude Code's agent + skill markdown shape.
  We don't borrow Claude Code's runtime: no plan mode, no TodoWrite, no
  Task tool, no MultiEdit, no permission modes.
- **Skills as prompt-injected metadata** — Anthropic's emerging
  SKILL.md / Skills convention: progressive disclosure via a
  `<system-reminder>` block listing names + descriptions, body and
  tools materialised only when the model invokes `load_skill`.
- **Plugin namespacing** — `<package>:<name>` prefix mirrors Claude
  Code's `<plugin>:<skill-name>` shape. Implemented via Python entry
  points so pip is the install mechanism.
- **Runtime semantics** — pi's loop shape (drain queues at the top of
  the turn, steering between tool calls, follow-up between turns) and
  pi's preference for files-as-truth and JSONL logs.
- **Hierarchical scopes** — both Claude Code and pi use scope walks;
  pyharness composes them in most-general-first order so concatenation
  produces the right precedence.

---

# Part 2 — Operational reference

> **Sync note.** This part is mirrored from
> [`packages/coding-harness/README.md`](packages/coding-harness/README.md).
> When code or behaviour changes, update both files in the same PR.
> Links below are root-relative to the repo so they work both in
> DESIGN.md and as a standalone reference.

`coding-harness` is the application built on the `pyharness-sdk`
kernel. Adapt it to your workflows, not the other way around, without
forking the loop. Extend it with [Skills](#skills),
[Extensions](#extensions), [Named Agents](#named-agents),
[Tools](#tools), and [Plugins](#plugins). Drop your customisations in
`.pyharness/` or publish them as a pip-installable package.

It runs in three modes: **CLI** for one-shot tasks, **SDK** for
embedding in your own apps, and via the bundled
[TUI](packages/tui/) for interactive dogfooding.

## Quick Start

```bash
pip install -e packages/pyharness-sdk -e packages/coding-harness

export ANTHROPIC_API_KEY=sk-ant-...   # or OPENAI_API_KEY, etc.

cd ~/work/my-project
pyharness init                        # drop a `.pyharness/` marker
pyharness "fix the failing tests"
```

`pyharness init` creates `.pyharness/` in the current directory with
a starter `settings.json` and empty `agents/`, `skills/`,
`extensions/`, `tools/` subdirectories. The marker is **required**:
running `pyharness "task"` walks up from the workspace looking for it
and refuses to run if none is found. This stops home-directory config
from leaking into unrelated sessions.

To bypass the requirement for one-off runs:

```bash
pyharness --bare "task"
```

`--bare` skips AGENTS.md, settings, and extensions entirely.

## CLI

```bash
# One-shot in cwd
pyharness "fix the failing tests"

# Different model or workspace
pyharness --model claude-opus-4-7 "review the diff"
pyharness --workspace /tmp/scratch "summarise this dir"

# Stream events as JSONL (good for parent processes)
pyharness --json "create a TODO list in TODO.md"

# Skip extensions / AGENTS.md / settings
pyharness --bare "task"

# Continue or resume
pyharness -c "follow up message"             # continue most-recent in cwd
pyharness -r                                 # list recent sessions for cwd
pyharness --session <id> "continue with X"   # resume specific session
pyharness --fork <id> "alternative path"     # fork from a session
pyharness --fork <id> --at-event 12 "..."    # fork at a specific event

# Inspect sessions
pyharness sessions ls
pyharness sessions show <id>
pyharness sessions replay <id>

# Run a named agent
pyharness --agent research-analyst "what changed in the markets today?"
```

## Concepts

### Workspace

The **operating directory** for the agent. Set with `--workspace`
(CLI) or `CodingAgentConfig.workspace`. Defaults to cwd. All file
tools resolve relative paths against it.

> Why a flag? `coding-harness` is SDK-first. A server or async
> orchestrator may run multiple agents concurrently in one process,
> each operating on a different directory. `os.chdir()` is
> process-global and races between async tasks. `workspace=` is the
> only safe way to isolate file work.

The **project root** is the closest ancestor of the workspace
containing a `.pyharness/` directory. It's a derived value, not a
separate input — pyharness walks up from `workspace` looking for the
marker and stops at `$HOME`.

**The project marker is required.** A `CodingAgent` constructed with
no `.pyharness/` discoverable above its workspace raises
`NoProjectError` immediately. Use `pyharness init` to create one, or
pass `bare=True` (CLI: `--bare`) to skip the project requirement.

This deliberate boundary stops home-directory config from leaking
into unrelated sessions — Claude Code's well-known failure mode.

**Two config scopes** for `.pyharness/<thing>` (settings, agents,
skills, tools, extensions):

| Scope | Path |
|---|---|
| Personal | `~/.pyharness/` (always) |
| Project | `<project_root>/.pyharness/` (the discovered marker) |

Most-general-first: project entries override personal on name
collision. There is **no** third "workspace" scope — if you want
workspace-local config, drop a `.pyharness/` in the workspace and it
becomes the project root automatically.

### AGENTS.md

`AGENTS.md` is read from `~/AGENTS.md` (personal) plus every
directory between project root and workspace, inclusive. The walk is
**bounded at project root** so guidance from above the marker can't
leak into unrelated sessions.

```
~/AGENTS.md                            # personal (always)
~/work/AGENTS.md                       # SKIPPED (above project root)
~/work/repo/AGENTS.md                  # project root
~/work/repo/src/AGENTS.md              # subdirectory rules
~/work/repo/src/components/AGENTS.md   # subpackage rules (workspace)
```

If your workspace is `~/work/repo/src/components/`, four files are
concatenated in general-first order so the most-specific ones appear
last and override. `~/work/AGENTS.md` is not read because it's above
the project marker.

Lines starting with `@<path>` are **deferred imports** — not inlined:

```markdown
# AGENTS.md
Top-level guidance.

@docs/architecture.md
@~/notes/large-reference.md
```

Each `@<path>` is replaced with a one-line pointer telling the agent
"read this file on demand using the `read` tool." Imports are
resolved relative to the AGENTS.md they appear in.

### Settings

Two layers, later wins:

| Path | Scope |
|---|---|
| `~/.pyharness/settings.json` | Global |
| `<project_root>/.pyharness/settings.json` | Project |

Plus CLI flags as a third overriding layer. Example:

```json
{
  "default_model": "claude-opus-4-7",
  "summarization_model": "claude-haiku-4-5",
  "max_turns": 100,
  "compaction_threshold_pct": 0.8,
  "search_provider": "brave",
  "search_api_key_env": "BRAVE_API_KEY"
}
```

See `coding_harness.config.Settings` for the full key list.

## Customisation

### Named Agents

Markdown files at `<scope>/.pyharness/agents/<name>.md`. YAML
frontmatter declares identity, model, and per-role allowlists; the
body becomes the system prompt prefix.

```yaml
---
name: research-analyst
description: Pulls market data and writes a daily summary.
model: claude-opus-4-7         # optional; overrides settings.default_model
tools:                          # additional non-builtin tools (additive over builtins)
  - get_quote
extensions:                     # OPT-IN; missing => no extensions activate
  - cost-tracker
  - kill-switch
  - acme:pii-redactor          # entry-point plugin (package:name)
skills:                         # allowlist; missing or ["*"] = all in scope
  - market-data
---

You are a research analyst...   # body becomes the system prompt prefix
```

**Resolution rules:**

| Field | Semantics |
|---|---|
| `tools:` | Additive over builtins. Listing a builtin is a no-op. Empty / missing / `["*"]` = just builtins. Non-builtin names must resolve against project tools or skill modules. |
| `extensions:` | Strict allowlist. Missing field = no extensions. Names match `discover_extensions()` output, including `package:name` for plugins. |
| `skills:` | Allowlist for what appears in the prompt index AND can be loaded via `load_skill`. Missing or `["*"]` = all in scope visible. |

Run with `pyharness --agent research-analyst "..."`.

Place in `~/.pyharness/agents/`, `<project>/.pyharness/agents/`, or
ship via a [plugin](#plugins) to share.

### Skills

On-demand capability bundles at `<scope>/.pyharness/skills/<name>/`.
Each skill is a directory:

```
.pyharness/skills/market-data/
  SKILL.md      # frontmatter + body (instructions returned on load)
  tools.py      # optional: module exposing `TOOLS = [...]`
  hooks.py      # optional: module exposing `register(api)` (skill bundle)
```

Skills appear in the system prompt as a `<system-reminder>` block
listing names + one-line descriptions. The agent calls the
`load_skill` built-in tool when a description matches the task; the
tool dynamically imports the skill's `tools.py`, registers its tools
into the live registry, runs `hooks.py:register(api)` if present,
and returns the SKILL.md body as instructions.

**Live discovery.** `load_skill` re-walks the filesystem and entry
points on every call, so a skill installed mid-run (e.g. via a bash
call to `npx skills add ...`) is loadable on the very next call
without restarting the agent. Named-agent `skills:` allowlists still
apply — the contract holds even for newly-installed skills.

```markdown
---
name: market-data
description: Use when fetching real-time quotes or fundamentals.
tools: [get_quote, get_fundamentals]
---

When fetching market data, prefer get_quote for current prices and
get_fundamentals for ratios. Always cite the source.
```

Skill bundles (with a `hooks.py`) let a skill ship its own lifecycle
hooks alongside its tools — hooks register only when the skill
activates, so they don't leak into agents that never load it.

Place in `~/.pyharness/skills/`, `<project>/.pyharness/skills/`, or
ship via a [plugin](#plugins).

### Extensions

Python modules at `<scope>/.pyharness/extensions/<name>.py`. Each
exposes a `register(api)` that subscribes to lifecycle events:

```python
from pyharness import ExtensionAPI, HookOutcome

def register(api: ExtensionAPI) -> None:
    api.on("before_tool_call", _gate)

async def _gate(event, ctx):
    if event.payload["tool_name"] == "bash":
        return HookOutcome.deny("bash disabled in this project")
    return HookOutcome.cont()
```

> **Extensions are never auto-loaded.** Even if a `.py` file exists
> in `.pyharness/extensions/`, it stays dormant unless explicitly
> enabled via the named agent's `extensions:` frontmatter,
> `CodingAgentConfig.extensions_enabled`, or the
> `extra_extensions` programmatic overlay.

Why opt-in? Extensions can deny LLM calls, modify messages, and
register tools. That blast radius shouldn't be opt-out.
`discover_extensions()` always returns the catalog (so a TUI can list
what's available); activation is an explicit choice.

Place in `~/.pyharness/extensions/` or `<project>/.pyharness/extensions/`,
or ship via a [plugin](#plugins).

### Tools

Project-level Python tools at `<scope>/.pyharness/tools/<name>.py`.
Each module exposes `TOOLS = [...]` of `Tool` instances — they
become available to named agents that list them in `tools:`
frontmatter.

```python
# .pyharness/tools/market_data.py
from pydantic import BaseModel, Field
from pyharness import Tool, ToolContext

class _GetQuoteArgs(BaseModel):
    ticker: str = Field(description="Ticker symbol")

class GetQuote(Tool):
    name = "get_quote"
    description = "Fetch the current quote for a ticker."
    args_schema = _GetQuoteArgs

    async def execute(self, args, ctx: ToolContext):
        return {"price": 192.34}  # implement for real

TOOLS = [GetQuote()]
```

For tools that should always be available across all agents, use
[built-in tools](#built-in-tools) in this package. For domain
specialists, use this directory.

### Plugins

Skills and extensions can also ship from **pip-installed Python
packages** via standard entry points. No filesystem layout needed.

```toml
# Library's pyproject.toml
[project.entry-points."pyharness.skills"]
sec-filings = "acme.skills.sec_filings"

[project.entry-points."pyharness.extensions"]
pii-redactor = "acme.extensions:register_pii"
```

Entry-point plugins are auto-discovered and namespaced as
`<package>:<name>` (e.g. `acme-finance-tools:sec-filings`) so plain
filesystem names cannot collide. Skill imports stay lazy — the
library only loads when the model calls `load_skill`. Extension
entry points resolve only when activated.

> **Trust:** Entry-point plugins run arbitrary Python at import
> time. Pyharness does not sandbox plugins. Trust comes from your
> Python environment: `pip install` only what you trust.

Activate from a named agent:

```yaml
extensions:
  - acme-observability:cost-tracker
skills:
  - acme-finance-tools:sec-filings
```

Or programmatically:

```python
agent = CodingAgent(CodingAgentConfig(
    workspace=ws,
    extensions_enabled=["acme-observability:cost-tracker"],
    skills_enabled=["acme-finance-tools:sec-filings"],
))
```

## Programmatic Use (SDK)

`CodingAgent` is the public SDK class. The CLI is a thin wrapper
around it.

### Embed

```python
import asyncio
from pathlib import Path
from coding_harness import CodingAgent, CodingAgentConfig

async def main() -> None:
    # Workspace must be inside a project tree (a directory with
    # `.pyharness/` somewhere at or above it). Use `bare=True` for
    # ad-hoc runs without a project.
    agent = CodingAgent(CodingAgentConfig(
        workspace=Path("~/work/my-project").expanduser(),
        model="claude-opus-4-7",
    ))
    result = await agent.run("create hello.txt with the word 'hi'")
    print(result.final_output)

asyncio.run(main())
```

### Steering and follow-up

```python
handle = agent.start("deep research on X")
await handle.steer("also check Y")    # injected at next turn boundary
result = await handle.wait()
```

### Programmatic overlays

`CodingAgentConfig` accepts overlays so embedders don't need to
write files. Filesystem discovery still runs; overlays merge on top.

```python
from coding_harness import CodingAgent, CodingAgentConfig, SkillDefinition
from pyharness import ExtensionAPI

def my_extension(api: ExtensionAPI) -> None:
    api.on("after_tool_call", lambda event, ctx: print(event.payload))

agent = CodingAgent(CodingAgentConfig(
    workspace=Path("/tmp/scratch"),

    # Programmatic skills / tools / extensions (additive on top of fs)
    extra_skills=[SkillDefinition(name="my-skill", description="…", body="…")],
    extra_tools=[],
    extra_extensions=[my_extension],

    # Allowlist overrides (None = fall back to frontmatter / default)
    extensions_enabled=["cost-tracker"],
    skills_enabled=["my-skill", "market-data"],
))
```

### Subscribing to events directly

`agent.event_bus` is public — embedders can attach handlers without
defining an extension file:

```python
async def trace(event, ctx):
    print(event.name, event.payload)
    return None

for kind in ("before_tool_call", "after_tool_call", "after_llm_call"):
    agent.event_bus.subscribe(kind, trace)
```

## Orchestration

`coding-harness` ships **no** `Pipeline` / `FanOut` framework.
Compose `CodingAgent` instances in plain Python. The single helper
is `agent_workspace()`:

```python
from coding_harness import agent_workspace

async with agent_workspace(base, "research", cleanup=False) as ws:
    agent = CodingAgent(CodingAgentConfig(workspace=ws))
    await agent.run(...)
```

See [`examples/orchestration/`](examples/orchestration/) for runnable
recipes:

- `pipeline.py` — sequential, agent A → artefact → agent B
- `fanout.py` — parallel agents with reduce
- `supervisor.py` — supervisor delegating to specialists via subprocess

## Sessions

Every run writes a JSONL log to
`~/.pyharness/sessions/<cwd-hash>/<session-id>.jsonl`. Each line is
one Pydantic event (`SessionStartEvent`, `AssistantMessageEvent`,
`ToolCallEndEvent`, …). The log is the durable record;
`Session.read_messages()` reconstructs the LLM transcript on resume.

```bash
pyharness sessions ls
pyharness sessions show <id>
pyharness sessions replay <id>
```

Resume / fork:

```bash
pyharness --session <id> "continue with X"
pyharness --fork <id> --at-event 12 "alternative path"
```

## Built-in Tools

| Tool | Notes |
|---|---|
| `read`, `write`, `edit` | File I/O. `edit` requires unique-occurrence replacement. |
| `bash` | Shell with a small list of catastrophic-pattern hard-blocks (`rm -rf /`, fork bombs, `dd` to block devices, …). |
| `grep` | Regex search; uses `rg` if installed, falls back to a Python implementation. |
| `glob` | Pathname pattern listing. |
| `web_search` | Configurable provider (Brave / Tavily / Exa). API key from `settings.search_api_key_env`. |
| `web_fetch` | HTTP GET with optional HTML extraction (requires `extract` extra for trafilatura). |
| `load_skill` | Loads an on-demand skill by name; auto-registered. |

Builtins are always registered. Frontmatter `tools:` *adds* to them,
never replaces.

## CLI Reference

### Modes

| Flag | Description |
|---|---|
| (default) | Print final output to stdout |
| `--json` | Stream events as JSONL on stdout |
| `--bare` | Skip extensions, AGENTS.md, settings |
| `--quiet` | Suppress non-final output |

### Model and turns

| Option | Description |
|---|---|
| `--model <id>` | Model id (overrides `settings.default_model`) |
| `--max-turns <n>` | Maximum turns before forced stop |

### Workspace and named agent

| Option | Description |
|---|---|
| `--workspace <path>` | Operating directory (default: cwd) |
| `--agent <name>` | Run as named agent at `.pyharness/agents/<name>.md` |

### Sessions

| Option | Description |
|---|---|
| `-c`, `--continue` | Continue most-recent session in cwd |
| `-r`, `--recent` | List recent sessions for cwd |
| `--session <id>` | Resume specific session |
| `--fork <id>` | Fork session into a new one |
| `--at-event <n>` | With `--fork`, fork at a specific event |
| `pyharness sessions ls` | List recent sessions |
| `pyharness sessions show <id>` | Print session JSONL |
| `pyharness sessions replay <id>` | Pretty-print session events |

### Project

| Command | Description |
|---|---|
| `pyharness init` | Create `.pyharness/` in the current directory with a starter `settings.json` and empty `agents/`, `skills/`, `extensions/`, `tools/` subdirectories |
| `pyharness init --path <dir>` | Initialise at the given directory instead of cwd |
| `pyharness init --force` | Overwrite an existing `settings.json` |

### Examples

```bash
# Read-only review with a non-default model
pyharness --model claude-opus-4-7 "review src/ for unused imports"

# Run a domain agent with a specific workspace
pyharness --workspace /finance --agent analyst "deep dive on AAPL"

# Fork and explore an alternative
pyharness --fork 7d3a... --at-event 14 "what if we used numpy instead?"
```

## See Also

- [`packages/coding-harness/`](packages/coding-harness/) — same content as Part 2; the canonical source
- [`packages/pyharness-sdk/`](packages/pyharness-sdk/) — kernel internals (loop, events, contracts)
- [`packages/tui/`](packages/tui/) — minimal interactive shell
- [docs/guides/build-finance-harness.md](docs/guides/build-finance-harness.md) — end-to-end domain harness recipe
- [docs/guides/build-autoresearch-harness.md](docs/guides/build-autoresearch-harness.md) — same recipe for research workflows
