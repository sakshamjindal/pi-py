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

### Build your own harness by subclassing `coding-harness`

`coding-harness` already provides every piece of scaffolding a
domain harness needs that isn't actually domain-specific: settings
hierarchy, AGENTS.md walking, project root discovery, named
sub-agents, on-demand skills, extension discovery, and the assembly
machinery that ties them to the SDK loop. **Reuse it.**

You only write what's actually domain-specific:

1. **Tools** — Pydantic-backed `pyharness.Tool` subclasses for your
   domain (e.g. for finance: `get_quote`, `place_order`).
2. **Settings extras** — a `FinanceSettings(coding_harness.Settings)`
   adding typed fields (`max_position_usd`, `enable_live_orders`).
3. **System prompt** — your domain's instructions.
4. **Always-on extensions** — risk gates, time budgets, audit logs
   subscribed to the event bus.
5. **A subclass of `CodingAgent`** that overrides 3-4 hooks
   (`BASE_SYSTEM_PROMPT`, `_settings_class`, `_default_tool_registry`,
   `_tool_timeouts`) and installs your extensions in `_setup`.
6. **A thin CLI** (~30 lines).

The whole harness ends up at ~100 lines plus tools.

End-to-end recipes:

- → [`docs/guides/build-finance-harness.md`](docs/guides/build-finance-harness.md)
- → [`docs/guides/build-autoresearch-harness.md`](docs/guides/build-autoresearch-harness.md)

→ The override surface in detail:
[`packages/coding-harness/README.md`](packages/coding-harness/README.md)
(see *Subclassing for a domain-specific harness*).

→ Kernel API for the tools and extensions you write:
[`packages/pyharness-sdk/README.md`](packages/pyharness-sdk/README.md)

#### When to start from `pyharness-sdk` directly instead

Skip `coding-harness` and build straight on the SDK only when your
harness genuinely **rejects** the file-convention shape — e.g. a
remote-orchestration harness with no workspace, or a streaming
harness whose "session" is a network connection rather than a JSONL
file. For domain harnesses that look like *"different prompt +
different tools + different guard rails"*, subclass.

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
