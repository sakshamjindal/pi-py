# pi-py docs

Long-form guides that don't fit in package READMEs.

## Guides

- [**Building a finance harness on pyharness-sdk**](guides/build-finance-harness.md)
  — end-to-end walkthrough of building a domain-specific harness
  for trading / portfolio analysis on the kernel. Tools, settings,
  risk-check extension, assembly layer, CLI.
- [**Building an autoresearch harness on pyharness-sdk**](guides/build-autoresearch-harness.md)
  — same recipe applied to long-horizon research. Disk-as-truth for
  notes and plan files, citation auditing, time budgets, multi-agent
  via subprocesses.

Both guides are recipes. Read them alongside
[`packages/coding-harness/`](../packages/coding-harness/) — the
in-tree worked example whose source you can read as the reference
implementation.

## Reference

- [`packages/pyharness-sdk/README.md`](../packages/pyharness-sdk/README.md)
  — kernel API, the loop diagram, lifecycle events, public surface.
- [`packages/coding-harness/README.md`](../packages/coding-harness/README.md)
  — what `pyharness "task"` actually does, file conventions,
  built-in tools.
- [`packages/tui/README.md`](../packages/tui/README.md) — the
  minimal REPL.
- [`../DESIGN.md`](../DESIGN.md) — design principles, the explicit
  refusals list, architecture overview.
