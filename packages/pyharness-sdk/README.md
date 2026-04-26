# pyharness-sdk

The kernel of pi-py. The agent loop, the LLM client, the tool ABC,
the session log, the event bus, the compactor — and nothing more.
**This is what you build on** when constructing a domain-specific
harness (finance, autoresearch, quant, …).

No file conventions. No settings layout. No CLI. No built-in tools.
Anything that imposes scoping rules belongs one layer up — see
[`coding-harness`](../coding-harness/) for the application built on
this kernel.

> Mirrors pi-mono's [`packages/agent`](https://github.com/badlogic/pi-mono/tree/main/packages/agent),
> with a single LLM provider surface (LiteLLM) folded in.

---

## Table of Contents

- [The Loop](#the-loop)
- [Quick Start](#quick-start)
  - [Minimal Agent](#minimal-agent)
  - [Custom Tool](#custom-tool)
  - [Live Steering](#live-steering)
  - [Extensions](#extensions)
- [What's in the Box](#whats-in-the-box)
- [Lifecycle Events](#lifecycle-events)
- [What's Not Here (Deliberately)](#whats-not-here-deliberately)
- [Termination](#termination)

---

## The Loop

`Agent.run(prompt)` (and `Agent.start(prompt)`) drive a straight-line
loop. One turn:

```
                 ┌─────────────────────┐
                 │ drain queues        │  steering messages first,
                 │ (steering / follow) │  then follow-up
                 └──────────┬──────────┘
                            │
                            v
                 ┌─────────────────────┐
                 │ maybe_compact       │  if tokens > threshold,
                 │ (Compactor)         │  summarise the middle
                 └──────────┬──────────┘
                            │
                            v
            emit before_llm_call ──── extension can deny
                            │
                            v
                 ┌─────────────────────┐
                 │ llm.complete(...)   │
                 └──────────┬──────────┘
                            │
                            v
            emit after_llm_call
                            │
                  ┌─────────┴─────────┐
                  │                   │
        no tool_calls           tool_calls present
                  │                   │
                  v                   v
            return RunResult    for each call:
                                 emit before_tool_call ─ extension can deny / replace
                                       │
                                       v
                                 execute_tool (validate args via Pydantic)
                                       │
                                       v
                                 emit after_tool_call
                                       │
                                       v
                                 if steering queue not empty: break, drain, retry turn
                            │
                            v
                  emit turn_end → next turn
```

Every step writes a typed event to the `Session` JSONL log and emits
a `LifecycleEvent` on the `EventBus`. The session log is the durable
record; the event bus is the live observation/intervention surface
for extensions.

---

## Quick Start

### Minimal Agent

```python
import asyncio
from pathlib import Path

from pyharness import (
    Agent, AgentOptions, EventBus, LLMClient, Session, ToolRegistry,
)

async def main() -> None:
    workspace = Path.cwd()
    agent = Agent(
        AgentOptions(model="claude-opus-4-7", max_turns=10),
        system_prompt="You are a minimal echo agent.",
        tool_registry=ToolRegistry(),       # no tools = LLM must reply directly
        session=Session.new(workspace),
        event_bus=EventBus(),
        workspace=workspace,
        llm=LLMClient(),
    )
    result = await agent.run("say hello")
    print(result.final_output)

asyncio.run(main())
```

You supply system prompt, tools, session, and event bus. Everything
the loop needs is explicit.

### Custom Tool

```python
from pydantic import BaseModel, Field
from pyharness import Tool, ToolContext, ToolRegistry

class _AddArgs(BaseModel):
    a: int = Field(description="left")
    b: int = Field(description="right")

class AddTool(Tool):
    name = "add"
    description = "Add two integers."
    args_schema = _AddArgs

    async def execute(self, args, ctx: ToolContext):
        return {"sum": args.a + args.b}

registry = ToolRegistry()
registry.register(AddTool())
# pass registry to Agent(...)
```

The LLM sees the tool schema (generated from `args_schema` via
`Tool.to_openai_schema()`). Args are validated with Pydantic before
`execute` runs. Validation failures and exceptions become `ok=False`
tool results rather than crashes — the loop hands them back to the
LLM and lets it self-correct.

### Live Steering

```python
handle = agent.start("research X in depth")
await handle.steer("also cover Y")        # injected at the next turn boundary
await handle.follow_up_msg("note: skip Z")
result = await handle.wait()
```

| Method | When |
|---|---|
| `steer(text)` | Consumed at the top of the next turn |
| `follow_up_msg(text)` | Consumed between turns |
| `abort_event.set()` | Cuts the run after the in-flight tool call finishes |

### Extensions

```python
from pyharness import EventBus, ExtensionAPI, HookOutcome, ToolRegistry

bus = EventBus()
api = ExtensionAPI(bus=bus, registry=ToolRegistry(), settings=None)

async def on_tool(event, ctx):
    print(f"{event.payload['tool_name']}({event.payload['arguments']})")
    return HookOutcome.cont()

api.on("before_tool_call", on_tool)
# pass bus to Agent(...) — handlers fire on every tool call
```

| `HookOutcome` | Effect |
|---|---|
| `Continue` | Default; loop proceeds normally |
| `Deny(reason)` | Block the call; loop sees a denial |
| `Modify(new_event)` | Replace the event payload, continue |
| `Replace(value)` | Short-circuit with a synthetic result |

The first non-`Continue` outcome wins.

---

## What's in the Box

| Symbol | Role |
|---|---|
| `Agent`, `AgentOptions`, `AgentHandle` | The loop, its config, and live-steering handle |
| `LLMClient`, `LLMError`, `count_tokens` | Thin LiteLLM wrapper. Streaming canonical; `complete()` consumes the stream. Anthropic prompt caching applied automatically for Claude models. |
| `Tool`, `ToolRegistry`, `ToolContext`, `ToolError`, `execute_tool`, `safe_path` | The contract any tool must satisfy. Pydantic-validated args. |
| `Session`, `SessionInfo` | Append-only JSONL log; `new` / `resume` / `fork` / `read_messages`. |
| `MessageQueue` | Steering and follow-up queues, exposed through `AgentHandle`. |
| `EventBus`, `ExtensionAPI`, `HookOutcome`, `HookResult`, `HandlerContext`, `LifecycleEvent` | Extension runtime types. |
| `Compactor`, `CompactionResult` | Transparent context compaction. Keeps system + last N messages verbatim; summarises the middle via the cheaper `summarization_model`. |
| `Message`, `ToolCall`, `RunResult`, `LLMResponse`, `StreamEvent`, `TokenUsage` | Pydantic IO types. |
| Event subclasses | `SessionStartEvent`, `ToolCallEndEvent`, `AssistantMessageEvent`, … — the persisted JSONL payloads. |

For the complete export list see
[`src/pyharness/__init__.py`](src/pyharness/__init__.py).

---

## Lifecycle Events

Subscribable via `EventBus.subscribe(name, handler)`:

| Event | Fires |
|---|---|
| `session_start`, `session_end` | At the start / end of a run |
| `turn_start`, `turn_end` | At the boundary of each loop turn |
| `before_llm_call`, `after_llm_call` | Around each LLM completion |
| `before_tool_call`, `after_tool_call` | Around each tool invocation |
| `compaction_start`, `compaction_end` | Around context compaction |
| `steering_received`, `followup_received` | When a queued message is consumed |

Handlers run in registration order. Exceptions from handlers are
logged and skipped — extensions never crash the harness.

---

## What's Not Here (Deliberately)

- **No built-in tools.** Those live in `coding-harness`.
- **No `settings.json` loader.** You pass `AgentOptions` yourself.
- **No `AGENTS.md` walking.** The system prompt is whatever you hand in.
- **No named sub-agents, skills loader, or extension file discovery.**
  Application concerns; see `coding-harness`.
- **No CLI.** The kernel is a library.

This is on purpose. Anything that imposes file conventions or
scoping rules belongs one layer up.

> **For domain-specific harnesses** (finance, autoresearch, etc.),
> you typically don't need to use the SDK directly. Use
> `coding-harness` with a project directory containing your domain's
> tools, agents, skills, and extensions in `.pyharness/`. The
> project-as-files pattern lets you build any domain harness without
> writing assembly code.

---

## Termination

The loop terminates when:

| Condition | `RunResult.reason` |
|---|---|
| LLM returns no tool calls | `"completed"` (with `completed=True`) |
| `max_turns` reached | `"max_turns"` |
| `AgentHandle.abort_event.set()` called | `"aborted"` |
| LLM call raises or extension denies a turn | `"error"` |

`RunResult` is a Pydantic model — `final_output`, `reason`,
`completed`, plus token / cost accounting in `usage`.

---

## See Also

- [`coding-harness`](../coding-harness/) — application built on this kernel
- [`tui`](../tui/) — minimal interactive shell
- [`DESIGN.md`](../../DESIGN.md) — principles and architecture
- [docs/guides/build-finance-harness.md](../../docs/guides/build-finance-harness.md)
- [docs/guides/build-autoresearch-harness.md](../../docs/guides/build-autoresearch-harness.md)
