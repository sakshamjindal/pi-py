# pi-py

A Python agent harness designed as an embeddable primitive — for
autonomous, long-running, programmatically-driven use cases where the
harness is one component of a larger system, rather than a chat
product used directly by humans.

The intent is that **domain-specific harnesses are built on top of
pi-py** rather than reinventing the loop, the session log, the tool
ABC, and the extension surface every time.

## What's in the box

```
packages/
  pyharness-sdk/   # the kernel — agent loop, LLM client, tool ABC,
                   # sessions, queues, events, extension runtime.
                   # This is what you build your own harness on.

  coding-harness/  # one concrete harness built on the kernel: the
                   # bundled coding agent (settings, AGENTS.md,
                   # named sub-agents, skills, extensions discovery,
                   # built-in tools, CLI). Itself extensible.

  tui/             # stdlib REPL for dogfooding the coding harness.
```

- **`pyharness-sdk`** — the primitive. Use this when you're building
  a *new* harness for a specific domain (a finance harness, an
  autoresearch harness, …). It gives you the loop and the contracts;
  you supply the system prompt, tools, and conventions.
- **`coding-harness`** — a reference harness for coding tasks. Use
  this when you want pyharness as a ready-to-run coding agent. It
  also has its own extension and tool layer, so it can be specialised
  for a particular codebase or workflow without forking.
- **`pyharness-tui`** — minimal interactive shell for trying the
  coding harness from your terminal.

## How to run

```bash
git clone <this repo>
cd pi-py

pip install -e packages/pyharness-sdk \
            -e packages/coding-harness \
            -e packages/tui \
            -e ".[dev]"

export ANTHROPIC_API_KEY=sk-...   # or OPENAI_API_KEY, etc.

# bundled coding agent
pyharness "fix the failing tests"

# interactive REPL
pyharness-tui
```

That's enough to use the coding harness today. For flags, settings,
named agents, skills, and extensions, see
[`packages/coding-harness/README.md`](packages/coding-harness/README.md).

## Extending

### Build your own harness on `pyharness-sdk`

The SDK gives you the loop and primitives; everything else is your
choice. The recipe in four steps:

1. Define the tools your agent needs (e.g. for finance: price
   lookups, position queries, order placement) as `Tool` subclasses
   with Pydantic args.
2. Pick the file conventions for your domain — e.g. a
   `~/.finance-harness/` directory with strategy definitions,
   broker credentials, and audit settings.
3. Write a small assembly layer that loads those, builds a
   `ToolRegistry`, constructs a system prompt from your strategy
   files, and instantiates `pyharness.Agent`.
4. Subscribe extensions to the event bus for cross-cutting concerns
   (audit logging, P&L tracking, kill switches).

`coding-harness` is the worked example of this pattern — read its
source for the assembly shape. End-to-end recipes for two specific
verticals:

- → [`docs/guides/build-finance-harness.md`](docs/guides/build-finance-harness.md)
- → [`docs/guides/build-autoresearch-harness.md`](docs/guides/build-autoresearch-harness.md)

The same recipe applies to quant research, ops harnesses, and so on.

→ Kernel API surface, loop diagram, and public symbols:
[`packages/pyharness-sdk/README.md`](packages/pyharness-sdk/README.md)

### Extend `coding-harness` for a specific codebase or workflow

If your use case fits the coding-agent shape but needs custom
behaviour (a project-specific tool, a guard rail, a notification on
each run), you don't need a new harness — extend coding-harness:

- **Project tools** at `<project>/.pyharness/tools/<name>.py` —
  Python modules exposing a `TOOLS` list.
- **Skills** at `<project>/.pyharness/skills/<name>/` — on-demand
  capability bundles loaded via the `load_skill` tool.
- **Named sub-agents** at `<project>/.pyharness/agents/<name>.md` —
  alternate system prompts + tool subsets, invoked with `--agent`.
- **Extensions** at `<project>/.pyharness/extensions/<name>.py` —
  subscribe to lifecycle events; deny / replace / observe LLM calls
  and tool invocations.

→ Conventions, examples, and per-feature docs:
[`packages/coding-harness/README.md`](packages/coding-harness/README.md)

## Lineage

- **Runtime semantics from [pi](https://github.com/badlogic/pi-mono)** —
  minimal core, files-as-truth, observability over magic.
- **File formats from Claude Code** — frontmatter agent definitions,
  standardized skills layout.
- **Own choices** — Python (so tools are Python), LiteLLM for provider
  abstraction, headless and programmatic over interactive.

## Design

[`DESIGN.md`](DESIGN.md) — principles, the explicit refusals list
(plan mode, MultiEdit, MCP, in-loop sub-agent delegation, …), the
architecture overview, and what we borrowed from pi and Claude Code.

## More

[`docs/`](docs/README.md) — long-form guides, including the
finance-harness and autoresearch-harness build recipes.
