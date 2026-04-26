# coding-harness — coding-agent scaffolding

Builds on the [`pyharness`](../pyharness-sdk/) SDK kernel to provide
the out-of-the-box behaviour of the bundled coding agent: file
conventions (AGENTS.md, `.pyharness/` directories), settings
hierarchy, named sub-agents, skills, extensions discovery, eight
built-in tools, and the `pyharness` CLI.

Mirrors pi-mono's [`packages/coding-agent`](https://github.com/badlogic/pi-mono/tree/main/packages/coding-agent)
as the application layer. Use this when you want pi-py as a
ready-to-run coding agent. Skip it (and depend only on
`pyharness-sdk`) when building a domain-specific harness with its
own conventions — see
[`../pyharness-sdk/README.md`](../pyharness-sdk/README.md) and the
[extension guides](../../docs/guides/).

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
       │     if --agent NAME was passed:
       │         load <scope>/.pyharness/agents/NAME.md, parse
       │         frontmatter, resolve declared tool names against
       │         builtins → project tools → skill tools.
       │     else:
       │         builtin_registry()  — all eight defaults.
       │
       ├─ discover_skills(workspace_ctx)
       │     walk <scope>/.pyharness/skills/*/SKILL.md.
       │     Register `load_skill` tool last (so it can see the
       │     other tools to mutate the registry on demand).
       │
       ├─ _build_system_prompt()
       │     BASE_SYSTEM_PROMPT + AGENTS.md (from home → project →
       │     workspace) + agent body (if named) + skill index.
       │
       ├─ if not bare:
       │     ExtensionAPI(bus, registry, settings) and
       │     load_extensions() walks <scope>/.pyharness/extensions/,
       │     imports each module, calls register(api).
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

## Programmatic use

```python
import asyncio
from coding_harness import CodingAgent, CodingAgentConfig

async def main() -> None:
    agent = CodingAgent(CodingAgentConfig(
        workspace="/tmp/scratch",
        model="claude-opus-4-7",
    ))
    result = await agent.run("create hello.txt with the word 'hi'")
    print(result.final_output)

asyncio.run(main())
```

To steer or follow up while a run is in flight, use `agent.start(...)`:

```python
handle = agent.start("deep research on X")
await handle.steer("also check Y")
result = await handle.wait()
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

### Named sub-agents

Markdown files at `<scope>/.pyharness/agents/<name>.md`. YAML
frontmatter declares:

```yaml
---
name: research-analyst
description: Pulls market data and writes a daily summary.
model: claude-opus-4-7      # optional, overrides the default
tools:                       # optional; if empty, all builtins
  - read
  - write
  - web_search
  - web_fetch
workdir: ~/research          # optional default workspace
---

You are a research analyst...   # body becomes the system prompt
```

Discovered by `discover_agents()`; loaded by
`load_agent_definition()`; tool list resolved by
`resolve_tool_list()` against builtins → project tools (under
`.pyharness/tools/`) → skill tools.

### Skills

On-demand capability bundles at
`<scope>/.pyharness/skills/<name>/`. Layout:

```
.pyharness/skills/market-data/
  SKILL.md      # frontmatter + body (instructions injected on load)
  tools.py      # Python module exposing `TOOLS = [...]`
```

Skills appear in the system prompt's "Available skills" index.
The agent calls the `load_skill` built-in tool with the skill
name when the description matches the task; that tool registers
the skill's tools into the live registry and returns the body
as instructions.

### Extensions

Python modules at `<scope>/.pyharness/extensions/<name>.py`.
Each exposes a top-level `register(api)` that subscribes to
lifecycle events:

```python
from pyharness import ExtensionAPI, HookOutcome

def register(api: ExtensionAPI) -> None:
    api.on("before_tool_call", _gate)

async def _gate(event, ctx):
    if event.payload["tool_name"] == "bash":
        return HookOutcome.deny("bash disabled in this project")
    return HookOutcome.cont()
```

Loaded once at `CodingAgent.__init__`. Project extensions override
personal ones with the same name (project entries are loaded last,
so the last `register()` wins).

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
    LoadedExtensions, load_extensions,
    # built-in tools
    all_builtin_tools, builtin_registry, builtin_tool_names,
)
```
