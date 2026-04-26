# pi-py

A Python agent harness designed as an **embeddable primitive** — for
autonomous, long-running, programmatically-driven use cases where the
harness is one component of a larger system, rather than a chat
product used directly by humans.

The intent is that **domain-specific harnesses are built on top of
pi-py** rather than reinventing the loop, the session log, the tool
ABC, and the extension surface every time.

> Inspired by [pi-mono](https://github.com/badlogic/pi-mono):
> minimal core, files-as-truth, observability over magic. File
> formats from Claude Code (frontmatter agents, SKILL.md). Own
> choices: Python (so tools are Python), LiteLLM, headless and
> programmatic over interactive.

---

## Table of Contents

- [What's in the Box](#whats-in-the-box)
- [Quick Start](#quick-start)
- [Project-as-Files](#project-as-files)
- [Philosophy](#philosophy)
- [Documentation](#documentation)

---

## What's in the Box

```
packages/
  pyharness-sdk/   The kernel. Agent loop, LLM client, tool ABC,
                   sessions, queues, events, extension runtime.
                   Build your own harness on this.

  coding-harness/  Reference harness on top of the kernel. File
                   conventions, settings hierarchy, named agents,
                   skills, opt-in extensions, eight built-in tools,
                   plugin entry points, the `pyharness` CLI.

  tui/             Stdlib REPL for dogfooding the coding harness.
```

> **For most users, [`coding-harness`](packages/coding-harness/) is
> the entry point.** The SDK is for when you're building a *new*
> harness, not using one.

---

## Quick Start

```bash
git clone <this repo>
cd pi-py

pip install -e packages/pyharness-sdk \
            -e packages/coding-harness \
            -e packages/tui \
            -e ".[dev]"

export ANTHROPIC_API_KEY=sk-ant-...   # or OPENAI_API_KEY, etc.

# In your project directory, drop a `.pyharness/` marker
cd ~/work/my-project
pyharness init

# Bundled coding agent
pyharness "fix the failing tests"

# Interactive REPL
pyharness-tui
```

`pyharness init` creates `.pyharness/` in the current directory with
a starter `settings.json`. The marker is required — pyharness walks
up from the workspace looking for it and refuses to run if none is
found, so home-directory config can't accidentally leak into
unrelated sessions. Use `--bare` to bypass the requirement for
one-off runs.

For flags, settings, named agents, skills, extensions, and the SDK
API, see [`packages/coding-harness/README.md`](packages/coding-harness/README.md).

---

## Project-as-Files

**You don't subclass anything.** Set up a project directory with the
right files in `<project>/.pyharness/` and run
`pyharness --workspace /your/project --agent <name> "task"`.

pi-py is the engine. A "finance harness" or "autoresearch harness"
is just a project directory with domain-specific tools, agent
definitions, skills, extensions, and an `AGENTS.md` — files that
pi-py consumes. It doesn't know what domain it's running in.

```
/your-project/
  AGENTS.md                              # domain philosophy (supports `@import`)
  .pyharness/
    settings.json                        # model defaults, cost caps, domain keys
    agents/                              # named agent definitions (frontmatter)
      analyst.md
      reviewer.md
    tools/                               # domain tools (TOOLS = [...] in each module)
      market_data.py
      proposals.py
    skills/                              # on-demand capability bundles
      options-analysis/                  # SKILL.md + tools.py + (optional) hooks.py
    extensions/                          # opt-in lifecycle hooks
      audit_logger.py
      circuit_breaker.py
  workflows/                             # orchestration: plain Python driving CodingAgent
    morning_routine.py
```

Then drive it:

```python
from pathlib import Path
from coding_harness import CodingAgent, CodingAgentConfig

agent = CodingAgent(CodingAgentConfig(
    workspace=Path("/your-project"),
    agent_name="analyst",
))
result = await agent.run("deep dive on AAPL")
```

Or from the CLI:

```bash
pyharness --workspace /your-project --agent analyst "deep dive on AAPL"
```

Plug-ins, too: skills and extensions can ship as **pip-installable
packages** via Python entry points (`pyharness.skills`,
`pyharness.extensions`), so you can publish a domain library and let
others install it with `pip install acme-finance-tools`.

---

## Philosophy

pi-py is aggressively minimal so it doesn't dictate your workflow.

**No in-loop sub-agents.** Multi-agent runs are subprocesses; the
harness composes from the outside. Recipes in
[`examples/orchestration/`](examples/orchestration/).

**No plan mode, no `TodoWrite`, no `MultiEdit`.** Plans hide work
from the observability layer; agents already structure their work
via tool calls. The model writes plans to files like any other
artefact.

**No interactive permission prompts.** Tools execute or fail.
Approval gates would block scheduled and SDK-driven runs.

**No auto-loaded extensions.** Extensions affect the loop directly
(deny LLM calls, modify messages, register tools). Auto-load would
leak blast radius across roles. Opt in by name.

**No MCP in core.** The tool ABC is local Python; MCP can ship as an
extension.

See [`DESIGN.md`](DESIGN.md) for the full principles and explicit
refusals list.

---

## Documentation

| Doc | What |
|---|---|
| [`DESIGN.md`](DESIGN.md) | Design principles, refusals, architecture |
| [`packages/coding-harness/README.md`](packages/coding-harness/README.md) | The CLI, file conventions, SDK API, every flag |
| [`packages/pyharness-sdk/README.md`](packages/pyharness-sdk/README.md) | Kernel primitives and the loop diagram |
| [`packages/tui/README.md`](packages/tui/README.md) | Interactive shell |
| [`docs/guides/build-finance-harness.md`](docs/guides/build-finance-harness.md) | End-to-end walkthrough: 30-50 tools, 5 agents, morning routine |
| [`docs/guides/build-autoresearch-harness.md`](docs/guides/build-autoresearch-harness.md) | Same recipe for research workflows |
| [`docs/guides/plugins.md`](docs/guides/plugins.md) | Publishing skills and extensions from a pip-installed library |
| [`docs/guides/orchestration.md`](docs/guides/orchestration.md) | Pipeline, fan-out, supervisor patterns. Recipes, not a framework. |
