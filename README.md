# pyharness

A minimal Python agent harness for running LLM-driven agents on coding and
(later) finance tasks.

pyharness is plumbing, not a product. It takes a prompt, calls an LLM, runs
the tools the LLM requests, loops until done, and returns a result. It is
headless-first: the canonical interface is the CLI and the Python SDK, and
there is no TUI or interactive permission prompt.

This README is a stub during early development. See `DESIGN.md` for the
design principles, explicit refusals, and architecture overview that drive
this codebase.

## Status

Under construction. See the stage checkpoints in the build brief.
