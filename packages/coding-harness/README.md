# coding-harness — coding-agent scaffolding

Builds on the [`pyharness`](../pyharness-sdk/) SDK kernel to provide
the out-of-the-box behaviour of the bundled coding agent: file
conventions (AGENTS.md, `.pyharness/` directories), settings
hierarchy, named sub-agents, skills, extensions discovery, eight
built-in tools, and the `pyharness` CLI.

Mirrors pi-mono's [`packages/coding-agent`](https://github.com/badlogic/pi-mono/tree/main/packages/coding-agent)
as the application layer.

Despite the name, `coding-harness` is domain-agnostic. The assembly
layer — settings hierarchy, AGENTS.md walking, named agents, skills,
extensions discovery, tool resolution — works for any domain. The
`BASE_SYSTEM_PROMPT` is generic; domain identity comes from your
AGENTS.md and agent definitions.

To build a finance harness, an autoresearch harness, or any other
domain harness: set up a project directory with `.pyharness/` files
and point `CodingAgent` at it. No subclassing needed.

See the full guides:
- [`build-finance-harness.md`](../../docs/guides/build-finance-harness.md)
- [`build-autoresearch-harness.md`](../../docs/guides/build-autoresearch-harness.md)

---

## What happens when you run `pyharness "fix the failing tests"`

```
$ pyharness "fix the failing tests"
       │
       v
1.  cli.main() parses argv. The `sessions` subcommand is detected
    BEFORE argparse runs (because the prompt nargs="*" would
    swallow it). Otherwise it builds CodingAgentConfig from the
    flags + cwd and calls _run() under asyncio.

2.  CodingAgent(config) — the assembly layer. In __init__:
       │
       ├─ WorkspaceContext(workspace, project_root)
       │     finds the project root by walking up looking for
       │     .pyharness/, computes home + workspace + project
       │     scopes.
       │
       ├─ Settings.load(workspace, project_root, cli_overrides)
       │     reads ~/.pyharness/settings.json + <project>/.pyharness/
       │     settings.json + CLI overrides (in that merge order;
       │     last wins).
       │
       ├─ self.session = Session.new() | resume() | fork()
       │
       ├─ _build_tool_registry()
       │     Builtins are ALWAYS registered (8 defaults).
       │     If --agent NAME was passed: also resolve the agent's
       │     frontmatter `tools:` list against project tools / skill
       │     tools and pin those as additional always-on tools.
       │     `["*"]` or empty / missing means "just builtins."
       │
       ├─ discover_skills(workspace_ctx)
       │     walk <scope>/.pyharness/skills/*/SKILL.md AND query
       │     `pyharness.skills` Python entry points (pip-installed
       │     plugins). No skill code is imported at this stage.
       │     Filter the discovered set by the named agent's frontmatter
       │     `skills:` allowlist (or `["*"]` / missing = all). Merge
       │     `extra_skills` overlays from CodingAgentConfig.
       │     Register `load_skill` tool last (so it can see the other
       │     tools to mutate the registry on demand).
       │
       ├─ _build_system_prompt()
       │     BASE_SYSTEM_PROMPT + AGENTS.md (from home → project →
       │     workspace, with @import lines NOT inlined) + agent body
       │     (if named) + skill index rendered as a <system-reminder>
       │     block.
       │
       ├─ discover_extensions(...)
       │     walk <scope>/.pyharness/extensions/ AND query
       │     `pyharness.extensions` Python entry points. NOTHING is
       │     imported or activated at this stage; this is just a catalog.
       │
       ├─ if not bare:
       │     resolve the enabled extension list (CodingAgentConfig
       │     `extensions_enabled` overrides the named agent's
       │     frontmatter `extensions:` list; default is empty —
       │     extensions are NEVER auto-loaded).
       │     ExtensionAPI(bus, registry, settings) and load_extensions()
       │     imports each enabled module and calls register(api). Any
       │     `extra_extensions` callables on CodingAgentConfig also run.
       │
       ├─ LLMClient() + Compactor()
       │
       ├─ _build_agent()  — map Settings → AgentOptions and
       │     instantiate pyharness.Agent with the assembled
       │     system_prompt, tool_registry, session, event_bus,
       │     llm, compactor.
       │
       └─ self._agent = Agent(...)

3.  await agent.run(prompt) → delegates to self._agent.run()
       │
       v
   pyharness.Agent loop runs (see ../pyharness-sdk/README.md for
   the loop diagram). Every step writes to the JSONL session log
   and emits events on the bus. Extensions can deny/replace tool
   calls or LLM calls.

4.  _attach_human_stream subscribes to the bus and prints `  → toolname`
    on each tool call to stderr (or _attach_json_stream emits NDJSON
    on stdout if --json was passed).

5.  When the loop terminates, _run() prints result.final_output to
    stdout and the run-not-completed reason to stderr. Process
    exit code: 0 if completed else 1.
```

This is the recipe a domain-specific harness mirrors. See
[`build-finance-harness.md`](../../docs/guides/build-finance-harness.md)
for the same flow applied to a different vertical.

---

## CLI

```bash
# One-shot in the current directory.
pyharness "fix the failing tests"

# Specify model or workspace.
pyharness --model claude-opus-4-7 "review the code"
pyharness --workspace /tmp/scratch "summarise this directory"

# Stream events as JSONL on stdout (good for parent processes).
pyharness --json "create a TODO list in TODO.md"

# Skip extensions / AGENTS.md / settings.
pyharness --bare "task"

# Continue or resume.
pyharness -c "follow up message"               # continue most-recent in cwd
pyharness -r                                   # list recent sessions for cwd
pyharness --session <id> "continue with X"     # resume specific session
pyharness --fork <id> "alternative path"       # fork from a session
pyharness --fork <id> --at-event 12 "..."      # fork at a specific event

# Inspect sessions.
pyharness sessions ls
pyharness sessions show <id>
pyharness sessions replay <id>

# Run a named agent (defined in <scope>/.pyharness/agents/<name>.md).
pyharness --agent research-analyst "what changed in the markets today?"
```

## Programmatic use (SDK)

`CodingAgent` is the public SDK entry point. The CLI is a thin
wrapper around it; embed it directly in your own application.

### Basic embed

```python
import asyncio
from pathlib import Path
from coding_harness import CodingAgent, CodingAgentConfig

async def main() -> None:
    agent = CodingAgent(CodingAgentConfig(
        workspace=Path("/tmp/scratch"),
        model="claude-opus-4-7",
    ))
    result = await agent.run("create hello.txt with the word 'hi'")
    print(result.final_output)

asyncio.run(main())
```

### Steering and follow-up

```python
handle = agent.start("deep research on X")
await handle.steer("also check Y")
result = await handle.wait()
```

### Programmatic overlays

`CodingAgentConfig` accepts overlays so embedders don't need to write
files into `~/.pyharness/`. The filesystem still acts as the default
discovery source; overlays are merged on top.

```python
from coding_harness import (
    CodingAgent, CodingAgentConfig,
    SkillDefinition,
)
from pyharness import ExtensionAPI

# A skill defined in code, no filesystem entry.
in_memory_skill = SkillDefinition(
    name="my-skill",
    description="What this skill does.",
    body="When the model loads this skill, this body becomes instructions.",
)

def my_extension(api: ExtensionAPI) -> None:
    api.on("after_tool_call", lambda event, ctx: print(event.payload))

agent = CodingAgent(CodingAgentConfig(
    workspace=Path("/tmp/scratch"),
    extra_skills=[in_memory_skill],
    extra_tools=[],                     # always-on Tool instances
    extra_extensions=[my_extension],    # callables run with ExtensionAPI

    # Override allowlists (None means "fall back to frontmatter / default").
    extensions_enabled=["cost-tracker"],
    skills_enabled=["my-skill", "market-data"],
))
```

### Subscribing to lifecycle events directly

`agent.event_bus` is public — embedders attach handlers without
defining an extension file:

```python
agent = CodingAgent(...)

async def trace(event, ctx):
    print(event.name, event.payload)
    return None

for kind in ("before_tool_call", "after_tool_call", "after_llm_call"):
    agent.event_bus.subscribe(kind, trace)
```

### Multi-agent orchestration

`coding-harness` ships no `Pipeline` / `FanOut` framework. Compose
`CodingAgent` instances in plain Python; see
[`examples/orchestration/`](../../examples/orchestration/) for
sequential, fan-out, and supervisor recipes. The single helper is:

```python
from coding_harness import agent_workspace

async with agent_workspace(base, "research", cleanup=False) as ws:
    agent = CodingAgent(CodingAgentConfig(workspace=ws))
    await agent.run(...)
```

---

## Concepts (what each file convention means)

### Workspace, project root, scopes

- **Workspace** — the working directory for the agent. Chosen with
  `--workspace` (CLI) or `CodingAgentConfig.workspace`. Defaults to
  cwd. All file tools resolve relative paths against this.
- **Project root** — the nearest ancestor of the workspace
  containing a `.pyharness/` directory. Discovered by
  `WorkspaceContext.discover_project_root()` walking up from the
  workspace, stopping at `$HOME`.
- **Three scopes**, in most-general-first order:
  1. **Personal** — `~/.pyharness/`
  2. **Project** — `<project_root>/.pyharness/`
  3. **Workspace** — `<workspace>/.pyharness/` (if different from
     project root)

  All scope-aware lookups (AGENTS.md, settings, agents, skills,
  tools, extensions) walk the scopes general-first so concatenation
  produces the right precedence (more-specific overrides
  less-specific).

### AGENTS.md

Plain markdown at any scope's `AGENTS.md`. Concatenated into the
system prompt by `WorkspaceContext.render_agents_md()` in
home → project → workspace order. Use it for repo-specific
guidelines (style, conventions, do-nots).

Lines starting with `@` are treated as **deferred imports** and are
**not inlined** into the system prompt:

```markdown
# AGENTS.md

Top-level guidance.

@docs/architecture.md
@~/notes/large-reference.md

More guidance after the imports.
```

Each `@<path>` is replaced with a one-line pointer telling the agent
"read this file on demand using the `read` tool." Imports are
resolved relative to the AGENTS.md they appear in (or absolute via
`~`). Unresolved paths pass through as plain text so broken
references stay visible.

### Named sub-agents

Markdown files at `<scope>/.pyharness/agents/<name>.md`. YAML
frontmatter declares:

```yaml
---
name: research-analyst
description: Pulls market data and writes a daily summary.
model: claude-opus-4-7        # optional; overrides the default
tools:                         # optional non-builtin tools to pin always-on
  - get_quote                  # comes from a skill module or .pyharness/tools/
extensions:                    # OPT-IN; missing => no extensions activate
  - cost-tracker
  - kill-switch
  - acme:pii-redactor          # entry-point plugin, namespaced as package:name
skills:                        # allowlist for skills the agent can see
  - market-data                # missing or ["*"] => all in scope
workdir: ~/research            # optional default workspace
---

You are a research analyst...   # body becomes the system prompt
```

Resolution semantics:

- **`tools:`** — *additive over builtins*. Listing a builtin name is a
  no-op; missing / empty / `["*"]` means "just builtins." Non-builtin
  names must resolve against project tools (`.pyharness/tools/`) or
  skill tool modules.
- **`extensions:`** — strict allowlist. Missing field = no extensions
  activate. Names match those returned by `discover_extensions()`,
  including `package:name` for entry-point plugins.
- **`skills:`** — allowlist for which skills appear in the prompt
  index AND can be loaded via `load_skill`. Missing or `["*"]`
  means "all in scope are visible."

Discovered by `discover_agents()`; loaded by
`load_agent_definition()`; tool list resolved by
`resolve_tool_list()` against builtins → project tools → skill tools.

### Skills

On-demand capability bundles at
`<scope>/.pyharness/skills/<name>/`. Layout:

```
.pyharness/skills/market-data/
  SKILL.md      # frontmatter + body (instructions injected on load)
  tools.py      # optional: module exposing `TOOLS = [...]`
  hooks.py      # optional: module exposing `register(api)` (skill bundle)
```

Skills appear in the system prompt as a `<system-reminder>` block
listing names + one-line descriptions. The agent calls the
`load_skill` built-in tool when a description matches the task; that
tool dynamically imports the skill's `tools.py`, registers its tools
into the live registry, runs `hooks.py:register(api)` if present, and
returns the SKILL.md body as instructions. Skills already loaded in
the session are tracked on `LoadSkillTool.loaded_names` so the index
can render a "Loaded" / "Available" split.

Skill bundles (`hooks.py` next to `SKILL.md`) let a skill ship its
own lifecycle hooks alongside its tools. Hooks register only when the
skill is activated, so they don't leak into agents that never load
the skill.

### Plugin ecosystem (Python entry points)

Skills and extensions can also be published by **pip-installed
packages** via Python entry points. No filesystem layout needed.

```toml
# In a library's pyproject.toml
[project.entry-points."pyharness.skills"]
sec-filings = "acme.skills.sec_filings"     # dotted module path

[project.entry-points."pyharness.extensions"]
pii-redactor = "acme.extensions:register"   # dotted attribute
```

Entry-point plugins are auto-discovered by `discover_skills()` and
`discover_extensions()` and namespaced as `<package>:<name>` (e.g.
`acme:sec-filings`) to prevent collisions with filesystem entries
or other libraries. Skills entry points are **lazy** — the library
only imports when the model calls `load_skill`. Extensions, when
named in frontmatter or `extensions_enabled`, import eagerly at
session start.

### Extensions

Python modules at `<scope>/.pyharness/extensions/<name>.py` (or
entry-point plugins; see above). Each exposes a top-level
`register(api)` that subscribes to lifecycle events:

```python
from pyharness import ExtensionAPI, HookOutcome

def register(api: ExtensionAPI) -> None:
    api.on("before_tool_call", _gate)

async def _gate(event, ctx):
    if event.payload["tool_name"] == "bash":
        return HookOutcome.deny("bash disabled in this project")
    return HookOutcome.cont()
```

**Extensions are never auto-loaded.** Even if a `.py` file exists in
`.pyharness/extensions/`, it stays dormant unless explicitly enabled
via:

- the named agent's frontmatter `extensions: [...]` list, or
- `CodingAgentConfig.extensions_enabled=[...]` (programmatic), or
- `CodingAgentConfig.extra_extensions=[fn, ...]` (a callable that
  takes `ExtensionAPI`).

`discover_extensions()` always returns the catalog (so a TUI or
`pyharness list-extensions` can show what's available), but
activation is opt-in by name.

### Sessions

Every run writes a JSONL log to
`~/.pyharness/sessions/<cwd-hash>/<session-id>.jsonl`. Each line is
one Pydantic `AgentEvent` (`SessionStartEvent`,
`AssistantMessageEvent`, `ToolCallEndEvent`, …). The log is the
durable record. `Session.read_messages()` reconstructs the LLM
transcript from it on resume.

## Configuration (`settings.json`)

Locations, in merge order (later wins):

1. `~/.pyharness/settings.json` (personal)
2. `<project>/.pyharness/settings.json` (project)
3. CLI flags

Example:

```json
{
  "default_model": "claude-opus-4-7",
  "summarization_model": "claude-haiku-4-5",
  "max_turns": 100,
  "compaction_threshold_pct": 0.8,
  "search_provider": "brave",
  "search_api_key_env": "BRAVE_API_KEY",
  "fetch_timeout_seconds": 30
}
```

See `coding_harness.config.Settings` for the full set of keys and
defaults.

## Built-in tools

| Tool | Notes |
| --- | --- |
| `read`, `write`, `edit` | File I/O. `edit` requires unique-occurrence replacement. |
| `bash` | Shell with a small list of catastrophic-pattern hard-blocks (`rm -rf /`, fork bombs, `dd` to block devices, …). |
| `grep` | Regex search; uses `rg` if installed, falls back to a Python implementation. |
| `glob` | Pathname pattern listing. |
| `web_search` | Configurable provider (Brave / Tavily / Exa). Read API key from `settings.search_api_key_env`. |
| `web_fetch` | HTTP GET with optional HTML extraction (requires the `extract` extra for trafilatura). |
| `load_skill` | Loads an on-demand skill by name; auto-registered. |

## Examples

Top-level `examples/` directory in the repo:

- `examples/agents/research-analyst.md` — a named agent definition.
- `examples/extensions/cost_logger.py` — token-cost JSONL logger.
- `examples/extensions/audit_logger.py` — per-tool audit log.
- `examples/extensions/circuit_breaker.py` — env-var kill switch.
- `examples/skills/market-data/` — skill scaffold.
- `examples/orchestration/pipeline.py` — sequential multi-agent pipeline.
- `examples/orchestration/fanout.py` — parallel agents with reduce.
- `examples/orchestration/supervisor.py` — supervisor delegating via subprocess.

## Using coding-harness for non-coding domains

```python
# Drive a finance agent from Python
agent = CodingAgent(CodingAgentConfig(
    workspace=Path("/finance"),
    agent_name="research-analyst",
))
result = await agent.run("deep dive on AAPL")
```

Or from the CLI:

```bash
pyharness --workspace /finance --agent research-analyst "deep dive on AAPL"
```

Full walkthroughs:
[`build-finance-harness.md`](../../docs/guides/build-finance-harness.md),
[`build-autoresearch-harness.md`](../../docs/guides/build-autoresearch-harness.md).

## Public surface

```python
from coding_harness import (
    # main entry points
    CodingAgent, CodingAgentConfig, BASE_SYSTEM_PROMPT,
    # configuration
    Settings, WorkspaceContext,
    # named sub-agents
    AgentDefinition,
    discover_agents, load_agent_definition, resolve_tool_list,
    list_known_tool_names,
    # skills
    SkillDefinition, LoadSkillTool, LoadSkillResult,
    discover_skills, build_skill_index,
    # extensions
    AvailableExtensions, LoadedExtensions,
    discover_extensions, load_extensions,
    # built-in tools
    all_builtin_tools, builtin_registry, builtin_tool_names,
    # orchestration helper
    agent_workspace,
)
```
