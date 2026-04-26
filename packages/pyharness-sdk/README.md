# pyharness — agent SDK kernel

The kernel: agent loop, LLM client, tool ABC, sessions, queues,
events, and the extension runtime. **This package is what you build
on** when constructing a domain-specific harness (autoresearch,
finance, quant research, …) — it gives you the primitives without
imposing file conventions, settings layout, or a CLI.

Mirrors pi-mono's [`packages/agent`](https://github.com/badlogic/pi-mono/tree/main/packages/agent)
as the kernel layer, with a single LLM provider surface (LiteLLM)
folded in.

## What's in the box

- `Agent` / `AgentOptions` — the loop and its config.
- `LLMClient` — thin LiteLLM wrapper with Anthropic prompt caching.
- `Tool`, `ToolRegistry`, `ToolContext`, `execute_tool`, `safe_path`
  — the contract any tool must satisfy. Pydantic-validated args;
  errors flow back to the LLM as tool results, never as exceptions.
- `Session`, `SessionInfo` — append-only JSONL log; `new` / `resume`
  / `fork`.
- `MessageQueue`, `AgentHandle` — steering and follow-up.
- `EventBus`, `ExtensionAPI`, `HookOutcome`, `HookResult`,
  `HandlerContext`, `LifecycleEvent` — extension runtime types.
- `Compactor` — transparent context compaction (system + tail
  preserved, middle summarised).
- All persisted event types (`SessionStartEvent`, `ToolCallEndEvent`,
  …).

## What's NOT here (deliberately)

- No built-in tools — those live in `coding-harness`.
- No `settings.json` loader — you pass `AgentOptions` yourself.
- No `AGENTS.md` walking — the system prompt is whatever you hand in.
- No named sub-agents, no skills loader, no extension file
  discovery — those are application concerns in `coding-harness`.
- No CLI.

This is the kernel. Anything that imposes file conventions or scoping
rules belongs one layer up.

## Quick start: minimal agent

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

You supply system prompt, tools, session, and event bus. The loop
emits lifecycle events through the bus so extensions can deny /
replace / observe LLM calls and tool invocations.

## Quick start: custom tool

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

Args are validated with Pydantic before `execute` runs. Validation
failures and exceptions become `ok=False` tool results so the loop
can hand them to the LLM and let it self-correct.

## Quick start: steering a live run

```python
handle = agent.start("research X in depth")
await handle.steer("also cover Y")        # injected at the next turn boundary
await handle.follow_up_msg("note: skip Z")
result = await handle.wait()
```

`steer` is consumed at the top of the next turn; `follow_up_msg` is
consumed between turns; `abort_event.set()` cuts the run after the
in-flight tool call.

## Quick start: extension subscribing to events

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

- `session_start`, `session_end`
- `turn_start`, `turn_end`
- `before_llm_call`, `after_llm_call`
- `before_tool_call`, `after_tool_call`
- `compaction_start`, `compaction_end`
- `steering_received`, `followup_received`

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
