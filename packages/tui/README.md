# pyharness-tui

The most minimal TUI: a stdlib-only REPL for dogfooding the
`pyharness` coding agent.

Read a line → run the agent in the current workspace → print the
result → repeat. Tool calls are traced to stderr as `  → <name>`.

Loop behaviour is unaffected: the TUI is a passive subscriber to the
event bus (see `DESIGN.md`).

## Install

```bash
pip install -e packages/pyharness-sdk \
            -e packages/harness \
            -e packages/tui
```

## Usage

```bash
# Interactive REPL.
pyharness-tui

# One-shot.
pyharness-tui "fix the failing tests"
```

In the REPL, type `exit` / `quit` or hit Ctrl-D to leave.
