# pi-py docs

Long-form material that doesn't fit in package READMEs.

## Guides

| Guide | What |
|---|---|
| [Building a finance harness](guides/build-finance-harness.md) | End-to-end recipe for a domain harness. 30–50 tools, 5 named agents, orchestrated morning routine, eval suite, feedback loop. |
| [Building an autoresearch harness](guides/build-autoresearch-harness.md) | Same recipe applied to long-horizon research. Disk-as-truth for notes and plan files, citation auditing, time budgets, multi-agent via subprocesses. |
| [Plugins](guides/plugins.md) | Publish skills and extensions from a pip-installed library via Python entry points (`pyharness.skills`, `pyharness.extensions`). Namespacing, lazy imports, activation rules, trust model. |
| [Orchestration](guides/orchestration.md) | Sequential pipelines, fan-out, supervisor / specialist via subprocess. Plain Python recipes, not a framework. |

Both build guides are recipes. Read them alongside
[`packages/coding-harness/`](../packages/coding-harness/) — the
in-tree worked example whose source you can read as the reference
implementation.

## Reference

| Doc | What |
|---|---|
| [`DESIGN.md`](../DESIGN.md) | Design principles, the explicit refusals list, architecture overview |
| [`packages/coding-harness/README.md`](../packages/coding-harness/README.md) | What `pyharness "task"` actually does, file conventions, every CLI flag, SDK API |
| [`packages/pyharness-sdk/README.md`](../packages/pyharness-sdk/README.md) | Kernel API, the loop diagram, lifecycle events |
| [`packages/tui/README.md`](../packages/tui/README.md) | The minimal REPL |
