# pyharness design

This document records the design principles and explicit refusals for
pyharness. Treat it as the answer key for "should we add X?" questions: if
X appears in the refusals list below, the answer is "as an extension, not
in core."

This file is a stub during early development. Stage 17 of the build brief
fills in the architecture overview and attribution sections. The principles
and refusals below are authoritative from day one.

## Design principles

1. **Headless-first.** Programmatic invocation (CLI + SDK) is canonical.
   No TUI, no interactive permission prompts, no chat-product features.
2. **Minimalism for the runtime, practical for capability.** Smaller core
   than Claude Code, slightly larger defaults than pi.
3. **Multi-vendor LLM, single-language harness.** LiteLLM handles
   providers. Python is the harness language because tools live in Python.
4. **Files-as-truth.** Session log on disk (JSONL) is the durable layer.
   The context window is working memory.
5. **Two ways to be an agent: place-based and name-based.** Both
   first-class. Workspace decoupled from identity.
6. **Always-on tools vs conditional skills.** Routine tools are declared in
   agent frontmatter at session start. Skills are reserved for genuinely
   conditional capabilities loaded on demand.
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

These features are rejected by design. If a future need genuinely requires
one, it lands as an extension, not core code.

- TUI of any kind.
- Interactive permission prompts.
- In-loop sub-agent delegation (Task tool, delegate tool).
- Plan mode.
- TodoWrite tool (the agent uses file tools).
- MultiEdit tool (single Edit only).
- Approval gates (tools execute or fail; no human-in-the-loop pauses).
- Doom-loop detection.
- Cost budget enforcement (logging is enough; deferred to v2).
- Sandbox provider abstraction (direct execution + hard-blocks for v1).
- Permission modes (no `acceptEdits`, no `bypassPermissions`).
- Custom slash commands.
- MCP support.

## Architecture (placeholder)

To be filled in at Stage 17.
