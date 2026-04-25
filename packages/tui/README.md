# pyharness-tui — placeholder

Intentionally empty. This package reserves the slot for an out-of-tree
TUI so that, when one is built, it can be a peer of `pyharness-sdk`
and `harness` — not a feature flag inside the kernel.

Headless-first is a core design principle (see `DESIGN.md` at the repo
root). The SDK and CLI must be exercisable in fully non-interactive
environments; rendering is a separable concern.
