# pyharness

A Python agent harness designed as an **embeddable primitive** —
optimized for autonomous, long-running, programmatically-driven use
cases where the harness is one component of a larger system, rather
than a chat product used directly by humans.

It exists as a building block. The intent is that domain-specific
harnesses (autoresearch, finance, quant research, …) compose pyharness
rather than reinventing the loop, the session log, the tool ABC, and
the extension surface every time.

## Lineage

- **Runtime semantics from [pi](https://github.com/badlogic/pi-mono)** —
  minimal core, files-as-truth, observability over magic.
- **File formats from Claude Code** — frontmatter agent definitions,
  standardized skills layout.
- **Own choices** — Python (so tools are Python), LiteLLM for provider
  abstraction, headless and programmatic over interactive.

## Layout

```
packages/
  pyharness-sdk/   # the SDK kernel: agent loop, LLM client, tool ABC,
                   # sessions, queues, events, extension runtime.
                   # Importable as `pyharness`.
  harness/         # coding-agent scaffolding on top of the SDK:
                   # settings, AGENTS.md, named sub-agents, skills,
                   # extensions discovery, built-in tools, CLI.
  tui/             # minimal stdlib REPL for dogfooding the agent.
                   # Importable as `pyharness_tui`.
```

Each package has its own README with quick starts, concepts, and APIs.

## Install (development)

```bash
pip install -e packages/pyharness-sdk \
            -e packages/harness \
            -e packages/tui \
            -e ".[dev]"
pytest -q
```

Set a provider key for the model you want
(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, …). LiteLLM picks them up
automatically.

## Where to go next

- Building a domain-specific harness on the kernel?
  → [`packages/pyharness-sdk/`](packages/pyharness-sdk/README.md)
- Want to use the bundled coding agent (CLI + built-in tools)?
  → [`packages/harness/`](packages/harness/README.md)
- Just want to play with it interactively?
  → [`packages/tui/`](packages/tui/README.md)
- Curious why pyharness rejects features that look obvious?
  → [`DESIGN.md`](DESIGN.md)
