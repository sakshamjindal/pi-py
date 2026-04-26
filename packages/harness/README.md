# harness — coding-agent scaffolding

Builds on the [`pyharness`](../pyharness-sdk/) SDK kernel to provide
the out-of-the-box behaviour of the bundled coding agent: file
conventions (AGENTS.md, `.pyharness/` directories), settings
hierarchy, named sub-agents, skills, extensions discovery, eight
built-in tools, and the `pyharness` CLI.

Mirrors pi-mono's [`packages/coding-agent`](https://github.com/badlogic/pi-mono/tree/main/packages/coding-agent)
as the application layer. Use this when you want pyharness as a
ready-to-run coding agent. Skip it (and depend only on
`pyharness-sdk`) when building a domain-specific harness with its
own conventions.

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
from harness import CodingAgent, CodingAgentConfig

async def main() -> None:
    agent = CodingAgent(CodingAgentConfig(
        workspace="/tmp/scratch",
        model="claude-opus-4-7",
    ))
    result = await agent.run("create hello.txt with the word 'hi'")
    print(result.final_output)

asyncio.run(main())
```

`CodingAgent` reads settings, walks AGENTS.md, discovers skills,
loads extensions, builds the tool registry, and constructs a
`pyharness.Agent` to run the loop.

To steer or follow up while a run is in flight, use `agent.start(...)`:

```python
handle = agent.start("deep research on X")
await handle.steer("also check Y")
result = await handle.wait()
```

## Concepts

- **Workspace** — the working directory for the agent. Chosen with
  `--workspace` (CLI) or `CodingAgentConfig.workspace`. Defaults to
  cwd.
- **Project root** — the nearest ancestor of the workspace containing
  a `.pyharness/` directory. Project-scope settings, agents, skills,
  and extensions live here.
- **Named agents** — Markdown files with YAML frontmatter at
  `<scope>/.pyharness/agents/<name>.md`. Frontmatter declares model,
  tools, default workdir; body is the system prompt. Invoke with
  `--agent <name>`.
- **Skills** — on-demand capability bundles at
  `<scope>/.pyharness/skills/<name>/{SKILL.md,tools.py}`. The agent
  calls the `load_skill` tool when its description matches the task,
  which injects instructions and registers the skill's tools.
- **Extensions** — Python modules at `<scope>/.pyharness/extensions/`.
  Each exposes `register(api)` and subscribes to lifecycle events
  (`before_llm_call`, `after_tool_call`, …). Can deny, modify, or
  replace outcomes.
- **Sessions** — every run writes a JSONL log to
  `~/.pyharness/sessions/<cwd-hash>/<session-id>.jsonl`. The log is
  the durable record and is replayable.

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

See `harness.config.Settings` for the full set of keys and defaults.

## Built-in tools

- `read`, `write`, `edit` — file I/O.
- `bash` — shell with a small list of catastrophic-pattern hard-blocks.
- `grep` — regex search; uses `rg` if installed.
- `glob` — pathname pattern listing.
- `web_search` — configurable provider (Brave, Tavily, Exa).
- `web_fetch` — HTTP GET with optional HTML extraction.
- `load_skill` — load an on-demand skill by name.

## Examples

Top-level `examples/` directory in the repo:

- `examples/agents/research-analyst.md` — a named agent definition.
- `examples/extensions/cost_logger.py` — token-cost JSONL logger.
- `examples/extensions/audit_logger.py` — per-tool audit log.
- `examples/extensions/circuit_breaker.py` — env-var kill switch.
- `examples/skills/market-data/` — skill scaffold.

## Public surface

```python
from harness import (
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
