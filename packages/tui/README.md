# pyharness-tui

The most minimal TUI: a stdlib-only REPL for dogfooding the bundled
[`coding-harness`](../coding-harness/) coding agent.

Read a line → run the agent in the current workspace → print the
result → repeat. Tool calls are traced to stderr as `  → <name>`.

Loop behaviour is unaffected: the TUI is a passive subscriber to the
event bus. See [`DESIGN.md`](../../DESIGN.md) for why it lives in a
separate package rather than inside the SDK or coding-harness layers.

## Install

```bash
pip install -e packages/pyharness-sdk \
            -e packages/coding-harness \
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

## What this package deliberately does NOT do

- No multi-turn chat UI, no scrollback widget, no panels.
- No third-party dependencies (no Rich, no Textual).
- No flags beyond the prompt — model, workspace, etc. come from
  `settings.json` and the cwd.

If you want richer rendering, build a separate package alongside this
one. The kernel and coding-harness packages must stay terminal-independent.
