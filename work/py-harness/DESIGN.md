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

```
+---------------------+    +-----------------+    +---------------+
| CLI / SDK           |--->| Harness loop    |--->| LiteLLM       |
| (cli.py / __init__) |    | (harness.py)    |    | (llm.py)      |
+---------------------+    +-----------------+    +---------------+
                              |       |
                              v       v
                  +----------------+  +----------------+
                  | Tool registry  |  | Event bus      |
                  | (tools/base)   |  | (extensions)   |
                  +----------------+  +----------------+
                              |
                              v
                  +-------------------+
                  | Session log JSONL |
                  | (session.py)      |
                  +-------------------+
```

Subsystems:

- **`llm.py`** — thin LiteLLM wrapper. Streaming canonical; non-stream
  is sugar that consumes the stream. Anthropic prompt caching is
  applied when the model is a Claude/Anthropic model.
- **`tools/`** — `Tool` ABC, `ToolRegistry`, `ToolContext`, OpenAI-shape
  schema generator. `tools/builtin/` ships the eight defaults plus
  `load_skill`.
- **`workspace.py`** — discovers project root (nearest ancestor with
  `.pyharness/`), walks AGENTS.md in most-general-first order, and
  collects `<scope>/.pyharness/{agents,skills,tools,extensions}` dirs.
- **`config.py`** — `Settings` Pydantic model loaded from personal +
  project + CLI overrides.
- **`session.py`** — append-only JSONL with atomic writes. Supports
  resume and fork by event sequence number. Reconstructs LLM message
  history from the log.
- **`events.py`** — typed event payloads; the union of session-log
  events and lifecycle events.
- **`extensions.py`** — `EventBus`, `ExtensionAPI`, `HookOutcome`,
  loader. First non-Continue outcome wins; extension exceptions are
  logged and skipped.
- **`agents.py`** — frontmatter parser, agent discovery, tool
  resolution. Resolves declared tool names against builtins → project
  tools → skill tools.
- **`skills.py`** — `SkillDefinition`, discovery, the `load_skill`
  built-in tool.
- **`compaction.py`** — keeps system + last N messages; summarises the
  middle via the cheaper `summarization_model`.
- **`queues.py`** — `MessageQueue` and `HarnessHandle` for steering and
  follow-up.
- **`harness.py`** — the loop. Drains queues, maybe compacts, calls
  LLM, executes tools (checking the steering queue between each), loops.
- **`cli.py`** — argparse front-end.

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

Target: under 1500 lines for `src/pyharness/` excluding tests, examples,
and generated code. Anything that pushes past the budget should either
move to an extension or be cut.
