# pi-py docs

Long-form material that doesn't fit in package READMEs.

> **Read order matters.** Before any guide in this directory, read
> **[`packages/coding-harness/README.md`](../packages/coding-harness/README.md)**.
> That's the canonical user manual — kernel concepts (workspace, named
> agents, skills, extensions, plugins, the SDK API). The guides below
> *apply* those concepts; they don't re-derive them.

## Reference (read these first)

| Doc | What |
|---|---|
| **[`packages/coding-harness/README.md`](../packages/coding-harness/README.md)** ← **start here** | What `pyharness "task"` actually does. File conventions, every CLI flag, SDK API. The canonical user doc. |
| [`packages/pyharness-sdk/README.md`](../packages/pyharness-sdk/README.md) | Kernel API, the loop diagram, lifecycle events |
| [`DESIGN.md`](../DESIGN.md) | Design principles + explicit refusals list. Philosophy. |
| [`packages/tui/README.md`](../packages/tui/README.md) | The minimal REPL |

## Guides (example recipes — read kernel manual first)

These are **example applications** of the kernel concepts. They show
you how to *use* pi-py to build something concrete. They are not
substitutes for the coding-harness README.

| Guide | What | Prerequisite |
|---|---|---|
| [Building a finance harness](guides/build-finance-harness.md) | End-to-end domain-harness recipe applied to trading / portfolio analysis | coding-harness README |
| [Building an autoresearch harness](guides/build-autoresearch-harness.md) | Same recipe applied to long-horizon research | coding-harness README |

The build-* guides are the largest worked examples — full project
directories with 30+ tools, named agents, eval suites. They are *not*
where to learn the kernel; they are where to see the kernel applied at
scale.
