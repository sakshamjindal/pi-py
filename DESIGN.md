# pi-py design

Philosophy + refusals. **For implementation details, CLI usage, file
conventions, and the full SDK walkthrough, the canonical doc is**
[`packages/coding-harness/README.md`](packages/coding-harness/README.md).
This file is the *why*; that file is the *how*.

> **Comparable projects:** [pi-mono](https://github.com/badlogic/pi-mono)
> (TypeScript) — the runtime-semantics inspiration; [Claude
> Code](https://docs.claude.com/en/docs/claude-code) — the file-format
> inspiration (frontmatter agents, SKILL.md); the
> [agentskills.io](https://agentskills.io/) standard — the SKILL.md
> spec we adopt; [AGENTS.md](https://agents.md/) — the cross-vendor
> guidance file pyharness reads from every directory between the
> project root and the workspace.

## Where to read next

| You want | Read |
|---|---|
| What pi-py does, the CLI, file conventions, SDK API | **[packages/coding-harness/README.md](packages/coding-harness/README.md)** ← canonical user doc |
| Kernel internals (loop, events, contracts) | [packages/pyharness-sdk/README.md](packages/pyharness-sdk/README.md) |
| Plugin entry points (skills + extensions via pip) | [docs/guides/plugins.md](docs/guides/plugins.md) |
| Multi-agent orchestration patterns | [docs/guides/orchestration.md](docs/guides/orchestration.md) |
| Domain-harness recipes (finance, autoresearch) | [docs/guides/build-finance-harness.md](docs/guides/build-finance-harness.md) · [autoresearch](docs/guides/build-autoresearch-harness.md) |
| Top-level pitch | [README.md](README.md) |

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
6. **Always-on tools vs conditional skills.** Builtins are always
   registered. Routine non-builtin tools are pinned in agent frontmatter
   `tools:` (additive over builtins). Skills are reserved for genuinely
   conditional capabilities loaded on demand via `load_skill`.
7. **Extensions are opt-in, never auto-loaded.** Discovery surfaces a
   catalog (filesystem + Python entry points). Activation requires an
   explicit name in frontmatter `extensions:`, programmatic
   `extensions_enabled`, or a CLI flag. Filesystem presence alone never
   triggers `register(api)`.
8. **Multi-agent through process spawning.** No in-loop sub-agent
   delegation. Sub-agents are subprocess invocations.
9. **One operating directory; project boundary is required and
   explicit.** The user supplies `workspace`. Pyharness walks up
   from it to find a `.pyharness/` marker, which becomes the
   project root (a derived value, not a separate input). The walk
   stops at `$HOME`. **No marker anywhere above the workspace = hard
   error**, not a silent fallback to personal-only config — this
   prevents home-directory config from leaking into unrelated
   sessions (Claude Code's well-known failure mode). Two config
   scopes: personal (`~/.pyharness/`) and project (the discovered
   marker). AGENTS.md is read from `~/AGENTS.md` plus every
   directory between project root and workspace inclusive — bounded
   so guidance from outside the project tree never applies.
   `pyharness init` creates the marker; `--bare` skips the
   requirement entirely.
10. **Plugin ecosystem via Python entry points.** Pip-installed
    libraries publish skills (`pyharness.skills`) and extensions
    (`pyharness.extensions`) without writing into `~/.pyharness/`.
    Namespaced as `<package>:<name>`. No bespoke plugin manifest, no
    install command.
11. **Standard formats from Claude Code; semantics from pi.** Frontmatter
    format is Claude Code-compatible (incl. SKILL.md and `<system-reminder>`
    skill index injection); runtime semantics follow pi.
12. **Build the minimum that ships; defer features until concrete need
    appears.**

## How the principles manifest

The bridge from abstract principles to concrete code. Each subsection
points at the full treatment elsewhere — DESIGN.md doesn't duplicate
operational content.

### Workspace and the project boundary
One user-facing path (`workspace`); pyharness walks up to find a
`.pyharness/` marker. Without one → `NoProjectError` (or pass
`--bare`). Two config scopes: personal (`~/.pyharness/`) and project
(the discovered marker). AGENTS.md is read at *every* directory
between project root and workspace, bounded so home-adjacent guidance
can't leak.
**Full treatment:**
[Workspace and config scopes](packages/coding-harness/README.md#workspace-and-config-scopes),
[AGENTS.md](packages/coding-harness/README.md#agentsmd).

### Tools — always-on vs frontmatter-pinned
The eight builtins (`read`, `write`, `edit`, `bash`, `grep`, `glob`,
`web_search`, `web_fetch`) plus `load_skill` are always registered.
A named agent's `tools:` list is *additive* — pins additional
non-builtin tools (project tools or skill tool modules) on top.
Listing a builtin in `tools:` is a no-op.
**Full treatment:**
[Built-in Tools](packages/coding-harness/README.md#built-in-tools),
[Named Agents](packages/coding-harness/README.md#named-agents).

### Skills — on-demand capability bundles
Folders with `SKILL.md` + optional `tools.py` + optional `hooks.py`.
Discovered eagerly (filesystem + Python entry points), materialised
lazily on `load_skill`. Live re-discovery on every call so a skill
installed mid-run (e.g. via `npx skills add`) is loadable without
restart. Named-agent `skills:` allowlist still applies — the
contract holds.
**Full treatment:**
[Skills](packages/coding-harness/README.md#skills),
[docs/guides/plugins.md](docs/guides/plugins.md).

### Extensions — opt-in lifecycle hooks
Python modules registering on the event bus. **Never auto-loaded** —
must be explicitly named in the named agent's `extensions:`
frontmatter, programmatic `extensions_enabled`, or `extra_extensions`
callables. Discovery still runs unconditionally so the catalog is
queryable.
**Full treatment:**
[Extensions](packages/coding-harness/README.md#extensions).

### Plugin ecosystem
Skills and extensions can ship from pip-installed packages via
`pyharness.skills` and `pyharness.extensions` Python entry points.
Namespaced as `<package>:<name>` to prevent collisions. No bespoke
manifest, no install command — `pip install` is the install
mechanism.
**Full treatment:**
[Plugins](packages/coding-harness/README.md#plugins),
[docs/guides/plugins.md](docs/guides/plugins.md).

## Explicit refusals

These features are rejected by design. If a future need genuinely
requires one, it lands as an extension, not as core code.

- **TUI in the SDK or coding-harness package.** The agent loop must
  remain programmatic; its behaviour cannot depend on terminal state.
  A TUI is allowed only as a separate package (`packages/tui/`) that
  subscribes to the event bus and renders — never threading back into
  the SDK or coding-harness layers.
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
- **Doom-loop detection.** `max_turns` is enough. Heuristics that guess
  "stuck" are noise.
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
- **Plugin trust / sandboxing.** Entry-point plugins run arbitrary
  Python at import time. We rely on pip's install boundary; we do not
  gate plugins behind an allow list or signature check in v1.
- **Mid-run extension toggle.** Extensions are bound at session start
  only. Disabling mid-run would leave dangling tool references and
  partial-effect state. If a kill switch is needed, ship it as an
  extension that gates other extensions internally.

## Architecture

Three packages — a small kernel (`packages/pyharness-sdk`), one
concrete application built on it (`packages/coding-harness`), and a
stdlib REPL (`packages/tui`). The kernel is loop-only; conventions
(file discovery, settings, named agents, skills, extensions, tools,
CLI) live in the application. Domain-specific harnesses (finance,
autoresearch, …) are project directories with `.pyharness/` files —
no subclassing, no fork.

**Full code-flow walkthrough** (the assembly steps, what each module
does, where to make changes):
[What happens when you run `pyharness "fix the failing tests"`](packages/coding-harness/README.md#what-happens-when-you-run-pyharness-fix-the-failing-tests)
in the coding-harness README.

## What we borrowed

- **Frontmatter format** — Claude Code's agent + skill markdown shape.
  We don't borrow Claude Code's runtime: no plan mode, no TodoWrite, no
  Task tool, no MultiEdit, no permission modes.
- **Skills as prompt-injected metadata** — Anthropic's emerging
  SKILL.md / Skills convention: progressive disclosure via a
  `<system-reminder>` block listing names + descriptions, body and
  tools materialised only when the model invokes `load_skill`.
- **Plugin namespacing** — `<package>:<name>` prefix mirrors Claude
  Code's `<plugin>:<skill-name>` shape. Implemented via Python entry
  points so pip is the install mechanism.
- **Runtime semantics** — pi's loop shape (drain queues at the top of
  the turn, steering between tool calls, follow-up between turns) and
  pi's preference for files-as-truth and JSONL logs.
- **Hierarchical scopes** — both Claude Code and pi use scope walks;
  pyharness composes them in most-general-first order so concatenation
  produces the right precedence.
