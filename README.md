# pyharness

A minimal Python agent harness for running LLM-driven agents on coding
and (later) finance tasks. Headless-first. Multi-vendor LLM via LiteLLM.
Plumbing, not a product.

pyharness takes a prompt, calls an LLM, runs the tools the LLM asks for,
loops until there are no tool calls left, and returns a result. Sessions
are durable JSONL on disk. Tools live in Python.

## Install

```bash
git clone <this repo>
cd py-harness
pip install -e ".[dev]"
```

Set a provider key in your environment for whichever model you plan to
use (e.g. `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`). LiteLLM picks up
provider keys automatically.

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

## Quick start (SDK)

```python
import asyncio
from pyharness import Harness, HarnessConfig

async def main():
    harness = Harness(HarnessConfig(
        workspace="/tmp/scratch",
        model="claude-opus-4-7",
    ))
    result = await harness.run("create hello.txt with the word 'hi'")
    print(result.final_output)
    print("session:", result.session_id)

asyncio.run(main())
```

To steer or follow up while a run is in flight, use `harness.start(...)`:

```python
handle = harness.start("deep research on X")
await handle.steer("also check Y")
result = await handle.wait()
```

## Concepts

- **Workspace** — the working directory for the agent. Chosen with
  `--workspace` (CLI) or `HarnessConfig.workspace` (SDK). Defaults to
  cwd.
- **Project root** — the nearest ancestor of the workspace containing a
  `.pyharness/` directory. Project-scope settings, agents, skills, and
  extensions live here.
- **Named agents** — Markdown files with YAML frontmatter at
  `<scope>/.pyharness/agents/<name>.md`. They declare model, tools,
  default workdir, and the system prompt body. Invoke with
  `--agent <name>`.
- **Skills** — on-demand capability bundles at
  `<scope>/.pyharness/skills/<name>/{SKILL.md,tools.py}`. The agent
  calls the `load_skill` tool when its description matches the task,
  which injects instructions and registers the skill's tools.
- **Extensions** — Python modules at `<scope>/.pyharness/extensions/`.
  They subscribe to lifecycle events (e.g. `before_llm_call`,
  `after_tool_call`) and can deny, modify, or replace outcomes.
- **Sessions** — every run writes a JSONL log to
  `~/.pyharness/sessions/<cwd-hash>/<session-id>.jsonl`. The log is the
  durable record of what happened and is replayable.

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

See `pyharness.config.Settings` for the full list of keys and defaults.

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

See `DESIGN.md` for the design principles, explicit refusals (TUI, plan
mode, MultiEdit, MCP, …), the architecture overview, and what we
borrowed from Claude Code and pi.

## Development

```bash
pip install -e ".[dev]"
pytest -q
```
