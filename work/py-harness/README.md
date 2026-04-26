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

## Extending: the project-as-files pattern

**You don't subclass anything.** You set up a project directory with the
right files in `<project>/.pyharness/` and run
`pyharness --workspace /your/project --agent <name> "task"`.

Pyharness is the engine. A "finance harness" or "autoresearch harness"
is just a project directory with domain-specific tools, agent definitions,
skills, extensions, and an AGENTS.md — files that pyharness consumes.
Pyharness doesn't know what domain it's running in.

The project layout:

```
/your-project/
  AGENTS.md                              # domain philosophy (investment rules, research standards, ...)
  .pyharness/
    settings.json                        # model defaults, cost caps, domain-specific keys
    agents/                              # named agent definitions
      analyst.md                         # frontmatter: name, model, tools list; body: system prompt
      reviewer.md
    tools/                               # domain tools (Python modules with TOOLS = [...])
      market_data.py                     # get_quote, get_fundamentals, ...
      proposals.py                       # propose_trade, flag_for_review, ...
    skills/                              # on-demand capability bundles
      options-analysis/{SKILL.md, tools.py}
    extensions/                          # lifecycle hooks
      audit_logger.py                    # register(api) -> subscribe to events
      circuit_breaker.py
  workflows/                             # orchestration: plain Python driving CodingAgent
    morning_routine.py
```

Then drive it:

```python
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

For full worked examples of this pattern:

- **Finance:** [`docs/guides/build-finance-harness.md`](docs/guides/build-finance-harness.md) —
  30-50 tools, 5 agents, orchestrated morning routine, eval suite, feedback loop.
- **Autoresearch:** [`docs/guides/build-autoresearch-harness.md`](docs/guides/build-autoresearch-harness.md) —
  research tools, literature review / synthesis / experiment agents, iterative research loop.

→ Conventions, per-feature docs:
[`packages/coding-harness/README.md`](packages/coding-harness/README.md)

→ SDK primitives and public API:
[`packages/pyharness-sdk/README.md`](packages/pyharness-sdk/README.md)

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
