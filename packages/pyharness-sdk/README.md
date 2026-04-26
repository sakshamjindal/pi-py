# pyharness — agent SDK kernel

The kernel of pi-py: agent loop, LLM client, tool ABC, sessions,
queues, events, extension runtime. **This is what you build on**
when constructing a domain-specific harness (finance, autoresearch,
quant research, …) — it gives you the primitives without imposing
file conventions, settings layout, or a CLI.

Mirrors pi-mono's [`packages/agent`](https://github.com/badlogic/pi-mono/tree/main/packages/agent)
as the kernel layer, with a single LLM provider surface (LiteLLM)
folded in.

---

## How the loop works

`pyharness.Agent.run(prompt)` (and `Agent.start(prompt)`) drive a
straight-line loop. One turn looks like:

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
record; the event bus is the *live* observation/intervention surface
for extensions.

The loop terminates when:
- The LLM returns no tool calls → `RunResult(completed=True, reason="completed")`.
- `max_turns` is reached → `RunResult(completed=False, reason="max_turns")`.
- `AgentHandle.abort_event.set()` is called → `reason="aborted"`.
- An LLM call raises or an extension denies a turn → `reason="error"`.

## What's in the box

| Symbol | Role |
| --- | --- |
| `Agent`, `AgentOptions` | The loop and its config. |
| `AgentHandle`, `MessageQueue` | Live steering and follow-up. |
| `LLMClient`, `LLMError`, `count_tokens` | Thin LiteLLM wrapper with Anthropic prompt caching. Streaming canonical; `complete()` consumes the stream. |
| `Tool`, `ToolRegistry`, `ToolContext`, `ToolError`, `execute_tool`, `safe_path` | The contract any tool must satisfy. Args validated via Pydantic before `execute`; failures and exceptions return `ok=False` so the loop can hand them to the LLM and let it self-correct. |
| `Session`, `SessionInfo` | Append-only JSONL log; `new` / `resume` / `fork` / `read_messages` for transcript reconstruction. |
| `EventBus`, `ExtensionAPI`, `HookOutcome`, `HookResult`, `HandlerContext`, `LifecycleEvent` | Extension runtime types. First non-Continue outcome wins. |
| `Compactor`, `CompactionResult` | Transparent context compaction. Keeps system + last N messages verbatim; summarises the middle via the cheaper `summarization_model`. |
| `Message`, `ToolCall`, `RunResult`, `LLMResponse`, `StreamEvent`, `TokenUsage` | Pydantic IO types. |
| Event subclasses (`SessionStartEvent`, `ToolCallEndEvent`, …) | Persisted JSONL event payloads. |

## What's NOT here (deliberately)

- No built-in tools — those live in `coding-harness`.
- No `settings.json` loader — you pass `AgentOptions` yourself.
- No `AGENTS.md` walking — the system prompt is whatever you hand in.
- No named sub-agents, no skills loader, no extension file
  discovery — those are application concerns in `coding-harness`.
- No CLI.

This is the kernel. Anything that imposes file conventions or scoping
rules belongs one layer up. See
[`build-finance-harness.md`](../../docs/guides/build-finance-harness.md)
and [`build-autoresearch-harness.md`](../../docs/guides/build-autoresearch-harness.md)
for end-to-end examples of one layer up.

---

## Quick starts

### Minimal agent

```python
import asyncio
from pathlib import Path

from pyharness import (
    Agent, AgentOptions, EventBus, LLMClient, Session, ToolRegistry,
)

async def main() -> None:
    options = AgentOptions(model="claude-opus-4-7", max_turns=10)
    workspace = Path.cwd()
    agent = Agent(
        options,
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

### Custom tool

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
# then pass `registry` into Agent(...)
```

The LLM sees the tool schema (generated from `args_schema` via
`Tool.to_openai_schema()`), so it knows the parameter shape. Args are
validated with Pydantic before `execute` runs. Validation failures
and exceptions become `ok=False` tool results rather than crashes.

### Live steering

```python
handle = agent.start("research X in depth")
await handle.steer("also cover Y")        # injected at the next turn boundary
await handle.follow_up_msg("note: skip Z")
result = await handle.wait()
```

`steer` is consumed at the top of the next turn; `follow_up_msg` is
consumed between turns; `abort_event.set()` cuts the run after the
in-flight tool call finishes.

### Extension subscribing to events

```python
from pyharness import EventBus, ExtensionAPI, HookOutcome, ToolRegistry

bus = EventBus()
api = ExtensionAPI(bus=bus, registry=ToolRegistry(), settings=None)

async def on_tool(event, ctx):
    print(f"{event.payload['tool_name']}({event.payload['arguments']})")
    return HookOutcome.cont()

api.on("before_tool_call", on_tool)
# pass `bus` into Agent(...) — handlers fire on every tool call
```

`HookOutcome.deny(reason)` blocks the call; `HookOutcome.replace(value)`
short-circuits with a synthetic result; the first non-`Continue`
outcome wins.

## Lifecycle events

Subscribable via `EventBus.subscribe(name, handler)`:

| Event | Fires |
| --- | --- |
| `session_start`, `session_end` | At the start / end of a run. |
| `turn_start`, `turn_end` | At the boundary of each loop turn. |
| `before_llm_call`, `after_llm_call` | Around each LLM completion. |
| `before_tool_call`, `after_tool_call` | Around each tool invocation. |
| `compaction_start`, `compaction_end` | Around context compaction. |
| `steering_received`, `followup_received` | When a queued message is consumed. |

`HookOutcome` values: `Continue`, `Deny`, `Modify`, `Replace`. The
first non-Continue outcome wins. `Modify` swaps the event payload
and continues; `Replace` short-circuits with a synthetic result for
LLM/tool calls; `Deny` blocks.

## Public surface

```python
from pyharness import (
    # loop
    Agent, AgentOptions, AgentHandle,
    # LLM
    LLMClient, LLMError, count_tokens,
    # tools
    Tool, ToolContext, ToolError, ToolRegistry,
    ToolExecutionResult, execute_tool, safe_path,
    # sessions
    Session, SessionInfo,
    # queues
    MessageQueue,
    # extensions runtime
    EventBus, ExtensionAPI, HookOutcome, HookResult,
    HandlerContext, LifecycleEvent,
    # compaction
    Compactor, CompactionResult,
    # types
    Message, ToolCall, RunResult, LLMResponse,
    StreamEvent, TokenUsage,
    # events
    AgentEvent, SessionStartEvent, SessionEndEvent,
    UserMessageEvent, AssistantMessageEvent,
    ToolCallStartEvent, ToolCallEndEvent,
    CompactionEvent, SteeringMessageEvent, FollowUpMessageEvent,
    SkillLoadedEvent, parse_event,
)
```
