# pyharness-tui

A minimal rich-formatted terminal UI for the pyharness coding agent.

It is a **passive subscriber** to the event bus: the loop runs
identically whether or not the TUI is attached. That preserves the
headless-first guarantee documented in `DESIGN.md` while still giving
interactive callers something nicer than the plain `pyharness` CLI's
stderr trace.

## Install

```bash
pip install -e packages/pyharness-sdk \
            -e packages/harness \
            -e packages/tui
```

## Usage

```bash
pyharness-tui "fix the failing tests"
pyharness-tui --model claude-opus-4-7 "review the code"
pyharness-tui --workspace /tmp/scratch --bare "summarise this directory"
```

## What you get

- A header panel with the model name.
- A green panel showing the prompt.
- Per-turn dividers.
- Colored tool-call traces (`→` start, `✓` success, `✗` failure).
- A blue panel for each assistant response.
- A final green panel with the run's result.

## Programmatic use

```python
import asyncio
from pathlib import Path

from pyharness_tui import run_tui

asyncio.run(run_tui(
    "do something",
    workspace=Path.cwd(),
    model="claude-opus-4-7",
))
```

Or attach the renderer to a `CodingAgent` you already built:

```python
from rich.console import Console
from harness import CodingAgent, CodingAgentConfig
from pyharness_tui import TuiRenderer

agent = CodingAgent(CodingAgentConfig(workspace=Path.cwd()))
TuiRenderer(Console()).attach(agent)
result = await agent.run("the task")
```

## What this package deliberately does NOT do

- It does not modify loop behaviour. No deny/replace hooks; only
  `Continue` outcomes.
- It does not depend on terminal state from inside the SDK or
  harness packages.
- It does not provide a chat-style multi-turn UI. One prompt, one
  result. For follow-ups, run again or use the SDK's steering API
  directly.
