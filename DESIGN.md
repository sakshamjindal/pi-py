# pyharness design

This document records the design principles, explicit refusals, and
architecture for pyharness. Treat the refusals list as the answer key
for "should we add X?": if X is on the list, the answer is "as an
extension, not in core."

## Design principles

1. **Headless-first.** Programmatic invocation (CLI + SDK) is canonical.
   No TUI, no interactive permission prompts, no chat-product features.
2. **Minimalism for the runtime, practical for capability.** Smaller
   core than Claude Code, slightly larger defaults than pi.
3. **Multi-vendor LLM, single-language harness.** LiteLLM handles
   providers; Python is the harness language because tools live in
   Python.
4. **Files-as-truth.** The session log on disk (JSONL) is the durable
   layer. The context window is working memory.
5. **Two ways to be an agent: place-based and name-based.** Both are
   first-class. Workspace is decoupled from identity.
6. **Always-on tools vs conditional skills.** Routine tools are declared
   in agent frontmatter at session start. Skills are reserved for
   genuinely conditional capabilities loaded on demand.
7. **Multi-agent through process spawning.** No in-loop sub-agent
   delegation. Sub-agents are subprocess invocations.
8. **Three scopes with hierarchical composition.** Personal
   (`~/.pyharness/`), project (`<project>/.pyharness/`), workspace (the
   directory).
9. **Standard formats from Claude Code; semantics from pi.** Frontmatter
   format is Claude Code-compatible; runtime semantics follow pi.
10. **Build the minimum that ships; defer features until concrete need
    appears.**

## Explicit refusals

These features are rejected by design. If a future need genuinely
requires one, it lands as an extension, not as core code.

- **TUI in the SDK or coding-harness package.** The agent loop must
  remain programmatic; its behaviour cannot depend on terminal state.
  A TUI is allowed only as a **separate package** (`packages/tui/`,
  importable as `pyharness_tui`) that subscribes to the event bus and
  renders — never threading back into the SDK or coding-harness
  layers. The SDK and coding-harness packages stay headless.
- **Interactive permission prompts.** Tools execute or fail. Approval
  gates would block scheduled and SDK-driven runs.
- **In-loop sub-agent delegation** (Task tool, delegate tool). Multi-
  agent runs are subprocesses; the harness composes from the outside.
- **Plan mode.** Plans are a UX concept that hides work from the
  observability layer. The agent already structures its work via tool
  calls.
- **TodoWrite tool.** The agent manages plans by writing files like any
  other artefact.
- **MultiEdit tool.** Single Edit only — keeps the diff surface
  reviewable and the failure modes few.
- **Approval gates.** No human-in-the-loop pauses inside the loop.
- **Doom-loop detection.** `max_turns` is enough. Heuristics that
  guess "stuck" are noise.
- **Cost budget enforcement.** Logging is enough for v1; if budgets
  matter later, an extension subscribing to `after_llm_call` is the
  right place.
- **Sandbox provider abstraction.** Direct execution + a small list of
  hard-blocks for v1. Defer the abstraction until there is concrete
  evidence we need to swap implementations.
- **Permission modes** (`acceptEdits`, `bypassPermissions`). There is
  one mode: tools run.
- **Custom slash commands.** We are headless; there is no command line.
- **MCP support.** Out of scope for v1. The tool ABC is local Python;
  MCP can ship as an extension later.

## Architecture

The repo is a pi-mono–style monorepo with three packages. The split
mirrors pi-mono: a small kernel (`pyharness-sdk`, like
`packages/agent`) plus one concrete application built on it
(`coding-harness`, like `packages/coding-agent`) plus a separate TUI
package.

```
+--------------------+        +--------------------+        +------------+
| coding-harness CLI |  -->   | CodingAgent        |  -->   | Agent loop |
| (pyharness "task") |        | (assembly layer)   |        | (kernel)   |
+--------------------+        +--------------------+        +------------+
                                       |                          |
                                       v                          v
                          +-----------------------+   +----------------------+
                          | Settings + Workspace  |   | LLMClient (LiteLLM)  |
                          | + AGENTS.md + Skills  |   +----------------------+
                          | + Sub-agents + Tools  |              |
                          | + Extensions loader   |              v
                          +-----------------------+   +----------------------+
                                                      | ToolRegistry         |
                                                      | (Pydantic-validated) |
                                                      +----------------------+
                                                                 |
                                                                 v
                                                      +----------------------+
                                                      | EventBus             |
                                                      | (extension hooks)    |
                                                      +----------------------+
                                                                 |
                                                                 v
                                                      +----------------------+
                                                      | Session JSONL log    |
                                                      | (durable record)     |
                                                      +----------------------+
```

### `pyharness-sdk` (kernel — package `pyharness`)

The loop and its primitives. No file conventions, no settings, no
CLI. This is what a domain-specific harness builds on.

- **`loop.py`** — `Agent` and `AgentOptions`. The straight-line loop:
  drain steering / follow-up queues, maybe compact, call LLM, execute
  tools (checking steering between calls), repeat until the LLM
  returns no tool calls or `max_turns` is hit. `Agent.start()`
  returns an `AgentHandle` for live steering; `Agent.run()` is the
  blocking equivalent.
- **`llm.py`** — thin LiteLLM wrapper. Streaming canonical;
  non-stream is sugar that consumes the stream. Anthropic prompt
  caching is applied when the model is a Claude / Anthropic model.
- **`tools/base.py`** — `Tool` ABC, `ToolRegistry`, `ToolContext`,
  OpenAI-shape schema generator. `execute_tool` validates args via
  Pydantic; failures and exceptions become `ok=False` results so the
  loop hands them back to the LLM rather than crashing.
- **`session.py`** — append-only JSONL log with atomic writes.
  Supports resume and fork by event sequence number. Reconstructs LLM
  message history from the log on resume.
- **`events.py`** — typed event payloads; the union of session-log
  events (`SessionStartEvent`, `ToolCallEndEvent`, …) and lifecycle
  events (`LifecycleEvent`).
- **`extensions.py`** — `EventBus`, `ExtensionAPI`, `HookOutcome`,
  `HookResult`, `HandlerContext`. First non-Continue outcome wins;
  extension exceptions are logged and skipped. The file-discovery
  loader does NOT live here (it's a coding-harness concern).
- **`compaction.py`** — `Compactor`. Keeps system + last N messages;
  summarises the middle via the cheaper `summarization_model`.
- **`queues.py`** — `MessageQueue` and `AgentHandle` for steering and
  follow-up.
- **`types.py`** — `Message`, `ToolCall`, `LLMResponse`, `RunResult`,
  `TokenUsage`, `StreamEvent`. All Pydantic.

### `coding-harness` (application — package `coding_harness`)

Reads settings, walks AGENTS.md, discovers skills, loads extensions,
builds a tool registry, constructs a `pyharness.Agent`. One concrete
harness built on the kernel — same recipe applies to a finance or
autoresearch harness.

- **`coding_agent.py`** — `CodingAgent`, `CodingAgentConfig`, the
  `BASE_SYSTEM_PROMPT`. The assembly layer: `__init__` reads
  settings → resolves the named agent (or falls back to all
  built-ins) → discovers skills → registers `load_skill` →
  renders the system prompt → builds the `EventBus` and loads
  extensions → maps `Settings` to `AgentOptions` → instantiates
  `pyharness.Agent`.
- **`config.py`** — `Settings` Pydantic model loaded from personal +
  project + CLI overrides.
- **`workspace.py`** — discovers project root (nearest ancestor with
  `.pyharness/`), walks AGENTS.md in most-general-first order, and
  collects `<scope>/.pyharness/{agents,skills,tools,extensions}`
  dirs.
- **`agents.py`** — frontmatter parser, agent discovery, tool
  resolution. Resolves declared tool names against builtins → project
  tools → skill tools.
- **`skills.py`** — `SkillDefinition`, discovery, the `load_skill`
  built-in tool.
- **`extensions_loader.py`** — file walker that imports each
  `<scope>/.pyharness/extensions/<name>.py` and calls its
  `register(api)`.
- **`_loader.py`** — shared dynamic-import helper for tools and skill
  modules.
- **`tools/builtin/`** — the eight defaults: `read`, `write`, `edit`,
  `bash` (with hard-blocks), `grep`, `glob`, `web_search`,
  `web_fetch`.
- **`cli.py`** — argparse front-end. Provides the `pyharness` console
  script.

### `tui` (REPL — package `pyharness_tui`)

Stdlib-only REPL that subscribes to the event bus and prints. Loop
behaviour is unaffected; the TUI never threads back into the SDK or
coding-harness layers.

## Lifecycle events

Extensions can subscribe to:

- `session_start`, `session_end`
- `turn_start`, `turn_end`
- `before_llm_call`, `after_llm_call`
- `before_tool_call`, `after_tool_call`
- `compaction_start`, `compaction_end`
- `steering_received`, `followup_received`

`HookOutcome` values: `Continue`, `Deny`, `Modify`, `Replace`. The first
non-Continue outcome wins.

## What we borrowed

- **Frontmatter format** — Claude Code's agent + skill markdown shape.
  We don't borrow Claude Code's runtime: no plan mode, no TodoWrite, no
  Task tool, no MultiEdit, no permission modes.
- **Runtime semantics** — pi's loop shape (drain queues at the top of
  the turn, steering between tool calls, follow-up between turns) and
  pi's preference for files-as-truth and JSONL logs.
- **Hierarchical scopes** — both Claude Code and pi use scope walks;
  pyharness composes them in most-general-first order so concatenation
  produces the right precedence.

## Line budget

Target: under 1500 lines combined for `packages/pyharness-sdk/src/`
and `packages/coding-harness/src/`, excluding tests, examples, and
generated code. Anything that pushes past the budget should either
move to an extension or be cut.
