# pyharness

A minimal Python agent harness for running LLM-driven agents on coding
and (later) finance tasks. Headless-first. Multi-vendor LLM via LiteLLM.
Plumbing, not a product.

This repo is a pi-mono–style monorepo with three packages:

- **`packages/pyharness-sdk/`** — the SDK kernel. Agent loop, LLM
  client, Tool ABC, sessions, queues, events, extension runtime.
  Importable as `pyharness`.
- **`packages/harness/`** — the coding-agent scaffolding on top of
  the SDK: settings hierarchy, AGENTS.md walking, named sub-agents,
  skills, extensions discovery, the eight built-in tools, and the
  `pyharness` CLI.
- **`packages/tui/`** — the most minimal TUI: a stdlib REPL for
  dogfooding the agent. Ships the `pyharness-tui` console-script.
  Passive subscriber to the event bus — never threads back into the
  SDK or harness packages.

The SDK exposes only the pure agent loop, mirroring pi-mono's
`packages/agent`. The `harness` package mirrors pi-mono's
`packages/coding-agent`.

## Install (development)

```bash
git clone <this repo>
cd py-harness

pip install -e packages/pyharness-sdk \
            -e packages/harness \
            -e packages/tui \
            -e ".[dev]"
```

Set a provider key in your environment for whichever model you plan to
use (e.g. `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`).

## Quick start (CLI)

```bash
# One-shot in the current directory.
pyharness "fix the failing tests"

# Specify model or workspace.
pyharness --model claude-opus-4-7 "review the code"
pyharness --workspace /tmp/scratch "summarise this directory"

# Stream events as JSONL (good for parent processes).
pyharness --json "create a TODO list in TODO.md"

# Skip extensions / AGENTS.md / settings.
pyharness --bare "task"

# Continue or resume.
pyharness -c "follow up message"
pyharness -r
pyharness --session <id> "continue with X"
pyharness --fork <id> "alternative path"

# Inspect sessions.
pyharness sessions ls
pyharness sessions show <id>
pyharness sessions replay <id>
```

## Dogfood (TUI REPL)

```bash
# Interactive: prompt, see result, prompt again, ctrl-d to quit.
pyharness-tui

# One-shot.
pyharness-tui "fix the failing tests"
```

Same agent loop as `pyharness`, just a thin REPL on top.

## Quick start (SDK kernel)

Use the kernel directly when you want full control over the system
prompt, tool registry, and assembly:

```python
import asyncio
from pathlib import Path

from pyharness import (
    Agent, AgentOptions, EventBus, LLMClient, Session, ToolRegistry,
)

async def main():
    options = AgentOptions(model="claude-opus-4-7", max_turns=10)
    workspace = Path.cwd()
    agent = Agent(
        options,
        system_prompt="You are a minimal echo agent.",
        tool_registry=ToolRegistry(),
        session=Session.new(workspace),
        event_bus=EventBus(),
        workspace=workspace,
        llm=LLMClient(),
    )
    result = await agent.run("hello")
    print(result.final_output)

asyncio.run(main())
```

## Quick start (harness scaffolding)

If you want pyharness's coding-agent defaults — settings.json, AGENTS.md,
named agents, skills, built-in tools — use the `harness` package:

```python
import asyncio
from harness import CodingAgent, CodingAgentConfig

async def main():
    agent = CodingAgent(CodingAgentConfig(
        workspace="/tmp/scratch",
        model="claude-opus-4-7",
    ))
    result = await agent.run("create hello.txt with 'hi'")
    print(result.final_output)

asyncio.run(main())
```

To steer or follow up while a run is in flight, use `agent.start(...)`:

```python
handle = agent.start("deep research on X")
await handle.steer("also check Y")
result = await handle.wait()
```

## Concepts

- **Workspace** — the working directory for the agent. Chosen with
  `--workspace` (CLI) or `CodingAgentConfig.workspace` (programmatic).
- **Project root** — the nearest ancestor of the workspace containing a
  `.pyharness/` directory. Project-scope settings, agents, skills, and
  extensions live here.
- **Named agents** — Markdown files with YAML frontmatter at
  `<scope>/.pyharness/agents/<name>.md`. Invoke with `--agent <name>`.
- **Skills** — on-demand capability bundles at
  `<scope>/.pyharness/skills/<name>/{SKILL.md,tools.py}`.
- **Extensions** — Python modules at `<scope>/.pyharness/extensions/`.
- **Sessions** — every run writes a JSONL log to
  `~/.pyharness/sessions/<cwd-hash>/<session-id>.jsonl`.

## Configuration (`settings.json`)

Locations, in merge order (later wins):

1. `~/.pyharness/settings.json` (personal)
2. `<project>/.pyharness/settings.json` (project)
3. CLI flags

See `harness.config.Settings` for the full list of keys.

## Built-in tools

- `read`, `write`, `edit` — file I/O.
- `bash` — shell with a small list of catastrophic-pattern hard-blocks.
- `grep` — regex search; uses `rg` if installed.
- `glob` — pathname pattern listing.
- `web_search` — configurable provider (Brave, Tavily, Exa).
- `web_fetch` — HTTP GET with optional HTML extraction.
- `load_skill` — load an on-demand skill by name.

## Examples

- `examples/agents/research-analyst.md` — a named agent definition.
- `examples/extensions/cost_logger.py` — token-cost JSONL logger.
- `examples/extensions/audit_logger.py` — per-tool audit log.
- `examples/extensions/circuit_breaker.py` — env-var kill switch.
- `examples/skills/market-data/` — skill scaffold.

## Design

See `DESIGN.md` for design principles, explicit refusals (TUI in core,
plan mode, MultiEdit, MCP, …), the architecture overview, and what
we borrowed from Claude Code and pi.

## Development

```bash
pip install -e packages/pyharness-sdk \
            -e packages/harness \
            -e packages/tui \
            -e ".[dev]"
pytest -q
```
