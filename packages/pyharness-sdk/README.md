# pyharness-sdk

The kernel of pi-py. Stateful agent with tool execution and durable
session logs. Built on LiteLLM.

Mirrors pi-mono's [`packages/agent`](https://github.com/badlogic/pi-mono/tree/main/packages/agent),
folded around our durable JSONL session log and built-in compaction.

## Installation

```bash
pip install pyharness   # or: uv pip install -e packages/pyharness-sdk
```

## Quick Start

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
        system_prompt="You are a helpful assistant.",
        tool_registry=ToolRegistry(),
        session=Session.new(workspace),
        event_bus=EventBus(),
        workspace=workspace,
        llm=LLMClient(),
    )
    result = await agent.run("Hello!")
    print(result.final_output)

asyncio.run(main())
```

`Agent` is the high-level wrapper. It owns the transcript, the session
log, the steering / follow-up queues, the abort event, and lifecycle
event fan-out. Use `agent_loop()` directly (see [Low-Level API](#low-level-api))
when you need a different lifecycle.

---

## Core Concepts

### Two-tier API

| Layer | Symbols | Role |
|---|---|---|
| Low-level | `agent_loop`, `agent_loop_continue`, `LoopConfig`, `LoopResult` | Pure coroutines. No state, no I/O assumptions. Caller supplies session appender, lifecycle emitter, queue drainers, abort event. |
| High-level | `Agent`, `AgentOptions`, `AgentHandle` | Adds the typical lifecycle: durable JSONL session, queues, abort, lifecycle bus, compaction, cost accumulation. |

### Message flow

```
session log (JSONL) ──┐
                      │       compaction
prior messages    ────┼─────► (if over threshold)
                      │             │
extra messages    ────┘             v
                              messages: list[Message]  ──► LLM ──► response
                                       ▲                              │
                                       │                              v
                                       └────────── tool_calls ◄── tool dispatch
```

The transcript is `list[pyharness.Message]` — OpenAI/LiteLLM shape:
`{role, content, tool_calls?, tool_call_id?, name?}`. Roles are
`system`, `user`, `assistant`, `tool`. The kernel never invents
new role types — application-level message kinds (skill notices,
UI markers) belong in your event bus or session events, not the
LLM transcript.

---

## Event Flow

The agent emits two parallel streams:

1. **Session events** persisted as JSONL via `Session.append_event(...)`.
   These are the durable record. `Session.read_events()` and
   `Session.read_messages()` reconstruct from disk.
2. **Lifecycle events** delivered through `EventBus` to handlers
   (extensions, observers). These are live-only.

### `agent.run("Hello")` — no tools

```
agent.run("Hello")
├─ session_start                    {cwd, model, agent_name, prompt_hash}
├─ user_message                     {content: "Hello"}
├─ lifecycle: turn_start            {turn: 1}
├─ lifecycle: before_llm_call       {messages: [...]}
├─ assistant_message                {text: "...", thinking: "...", tool_calls: []}
├─ lifecycle: after_llm_call        {response: {...}}
├─ lifecycle: turn_end              {turn: 1}     # only when tool batch ran
└─ session_end                      {reason: "completed", final_message: "..."}
```

### `agent.run("Read config.json")` — with tools

```
agent.run("Read config.json")
├─ session_start
├─ user_message
├─ lifecycle: turn_start            {turn: 1}
├─ lifecycle: before_llm_call
├─ assistant_message                {tool_calls: [{id, name, arguments}]}
├─ lifecycle: after_llm_call
│
│   ── tool batch dispatch (3 phases) ──
│
├─ lifecycle: before_tool_call      {tool_name, arguments, call_id}     # preflight, sequential
├─ tool_call_start                  {call_id, tool_name, arguments}     # execute
├─ tool_call_end                    {call_id, ok, result, duration_ms}
├─ lifecycle: after_tool_call       {tool_name, ok, result, terminate}
│   (… repeats per tool; in parallel mode, starts cluster before ends)
│
├─ lifecycle: turn_end              {turn: 1}
│
├─ lifecycle: turn_start            {turn: 2}
├─ assistant_message                {text: "the file contains…", tool_calls: []}
└─ session_end                      {reason: "completed"}
```

### Tool execution mode

| Mode | Behavior |
|---|---|
| `"sequential"` (default) | Tools run one at a time. Loop checks the steering queue between tool calls; a queued steer breaks the batch and re-runs the turn. |
| `"parallel"` | Preflight runs sequentially (validate args, run `before_tool_call`). Execution uses `asyncio.gather`. `tool_call_end` events fire in completion order. **`tool` messages are persisted in assistant source order** so the cache prefix stays deterministic. Steering only checked at batch boundary. |

Override per-tool via `Tool.execution_mode = "sequential"`. **If any
runnable call in a batch targets a sequential tool, the entire batch
runs sequentially regardless of the global setting.** Built-in
mutating tools (`bash`, `edit`, `write`) are tagged sequential.

### Terminate signal

A tool may return `ToolResult(content, terminate=True)` to hint that
the next LLM call should be skipped:

```python
async def execute(self, args, ctx):
    return ToolResult(content="done", terminate=True)
```

The loop short-circuits **only when every runnable tool in the
batch sets `terminate=True`**. Mixed batches (one terminal tool, one
read tool) continue normally so the LLM still sees the read result.

### `agent.continue_run()` — resume after error

`continue_run()` re-enters the loop without appending a new prompt.
Use it after `reason="error"` (LLM raised, network blip, rate limit)
or `reason="aborted"`.

```python
result = await agent.run("do the thing")
if result.reason == "error":
    result = await agent.continue_run()
```

The last non-system message must be `user` or `tool`. Continuing
from an `assistant` message would either send a malformed request or
duplicate the previous response, so the call refuses with `ValueError`.

The retry sends the same prefix as the failed call → 100% prompt-cache
hit on system + history. Synthesising a "please continue" user
message instead would invalidate the cache for every retry.

### Session-log event types (persisted JSONL)

| Event | Fields | Fires |
|---|---|---|
| `session_start` | `cwd, model, agent_name, system_prompt_hash, settings_snapshot` | First event of every session. |
| `user_message` | `content` | When the user sends a prompt or a queued message is consumed. |
| `assistant_message` | `text, thinking, tool_calls` | After every LLM completion. |
| `tool_call_start` | `call_id, tool_name, arguments` | Before each tool runs (after preflight). |
| `tool_call_end` | `call_id, tool_name, ok, result, error, duration_ms` | After each tool finishes. Also synthesised for unknown tools, denied calls, and skipped calls in an aborted batch. |
| `compaction` | `tokens_before, tokens_after, summary` | When the compactor rewrites the transcript. |
| `steering_message` | `content` | When a queued `steer()` is consumed. |
| `followup_message` | `content` | When a queued `follow_up_msg()` is consumed. |
| `skill_loaded` | `name, tools_added` | Application layer; emitted by the harness when a skill activates. |
| `session_end` | `reason, final_message` | Last event. `reason` ∈ {`completed`, `aborted`, `error`, `max_turns`}. |

Every event carries `event_id`, `session_id`, `timestamp`,
`sequence_number`. Writes are fsync'd before emission, so the JSONL
is the durable record even if the process crashes.

### Lifecycle event names (live-only, via EventBus)

| Name | Payload |
|---|---|
| `session_start`, `session_end` | `{prompt, model}` / `{reason, final_message}` |
| `turn_start`, `turn_end` | `{turn: int}` |
| `before_llm_call` | `{messages}` — handler may `Deny` to short-circuit |
| `after_llm_call` | `{response}` |
| `before_tool_call` | `{tool_name, arguments, call_id}` — handler may `Deny` or `Replace` |
| `after_tool_call` | `{tool_name, ok, result, duration_ms, terminate}` |
| `compaction_start`, `compaction_end` | `{}` / `{compacted, tokens_before, tokens_after}` |
| `steering_received`, `followup_received` | `{content}` |
| `message_start`, `message_end` | `{message: <Message dump>}` — fires around every transcript append (initial prompt, assistant message, tool result, steering / follow-up). Useful for streaming UIs and per-message extensions; the lifecycle stream parallels the durable session-log events. |

Subscribe via `EventBus.subscribe(name, handler)`. Handlers run in
registration order. The first non-`Continue` outcome wins. Handler
exceptions are logged and skipped — extensions never crash the
harness.

---

## AgentOptions

```python
AgentOptions(
    model="claude-opus-4-7",                # litellm model id
    max_turns=100,                          # hard ceiling on turns
    model_context_window=200_000,           # used to compute compaction threshold
    compaction_threshold_pct=0.8,           # compact when token estimate >= 80% of window
    tool_output_max_bytes=51_200,           # truncate tool output past this
    tool_output_max_lines=2_000,
    tool_timeouts={"bash": 30.0},           # per-tool wall-clock timeout
    max_tokens=None,                        # cap on assistant tokens per turn
    tool_execution="sequential",            # or "parallel"
    agent_name=None,                        # recorded in session_start
    settings_snapshot={},                   # passed through to extensions/tools
)
```

---

## Tools

### Defining a tool

```python
from pydantic import BaseModel, Field
from pyharness import Tool, ToolContext, ToolError, ToolResult

class _ReadArgs(BaseModel):
    path: str = Field(description="Path relative to the workspace.")

class ReadTool(Tool):
    name = "read"
    description = "Read a file from the workspace."
    args_schema = _ReadArgs
    execution_mode = "parallel"   # default; "sequential" forces batch serialisation

    async def execute(self, args, ctx: ToolContext):
        target = ctx.workspace / args.path
        if not target.is_file():
            raise ToolError(f"missing: {args.path}")
        return target.read_text(encoding="utf-8")
```

| Attribute | Purpose |
|---|---|
| `name` | Tool identifier the LLM sees. Required, unique. |
| `description` | Tool description — exposed to the LLM verbatim. |
| `args_schema` | Pydantic `BaseModel`. Args validated before `execute`. |
| `execution_mode` | `"parallel"` (default) or `"sequential"`. See [Tool execution mode](#tool-execution-mode). |
| `result_schema` | Optional Pydantic model for structured results. |

Tools may return:

- A **string** — used verbatim as the tool result content.
- A **dict** or **Pydantic model** — JSON-serialised.
- A **`ToolResult(content, terminate=False)`** — wrap any of the above and optionally hint termination.

### Errors

Raise `ToolError(...)` for recoverable, agent-actionable failures.
The message becomes the tool result text the LLM sees, with
`ok=False` on the session event. For fatal exceptions just `raise` —
the loop catches them, records the failure, and lets the LLM react.

```python
async def execute(self, args, ctx):
    if not ctx.workspace.exists():
        raise ToolError("workspace does not exist")
    ...
```

### Output truncation

Outputs exceeding `tool_output_max_bytes` or `tool_output_max_lines`
are truncated and the full payload is spilled to
`/tmp/pyharness-overflow/<session>/<tool>-<hash>.txt`. The truncated
result the LLM sees ends with the spill path so it can read more if
it needs to.

---

## Live Steering

```python
handle = agent.start("research X in depth")
await handle.steer("also cover Y")        # consumed at next turn boundary
await handle.follow_up_msg("note: skip Z") # consumed when agent would otherwise stop
await handle.abort()                       # cuts after in-flight tool call
result = await handle.wait()
```

| Method | When |
|---|---|
| `handle.steer(text)` | Drained at the **top of the next turn** in parallel mode; **between tool calls** in sequential mode. |
| `handle.follow_up_msg(text)` | Drained at turn top, alongside steering. Lets you queue work while the agent is busy. |
| `handle.abort()` | Sets `abort_event`. Loop bails after the in-flight tool call. |
| `handle.continue_run()` | Resume after the run ended in `error` / `aborted`. Resets `abort_event`. The previous task must be `done()`. |

Steering messages are recorded as `steering_message` events and
injected as `[steering] <content>` user messages. Follow-up messages
become plain user messages.

---

## Hooks (Extensions)

```python
from pyharness import EventBus, ExtensionAPI, HookOutcome, ToolRegistry

bus = EventBus()
registry = ToolRegistry()
api = ExtensionAPI(bus=bus, registry=registry, settings=None)

async def on_tool(event, ctx):
    name = event.payload["tool_name"]
    args = event.payload["arguments"]
    if name == "bash" and "rm -rf" in args.get("command", ""):
        return HookOutcome.deny("rm -rf is not allowed")
    return HookOutcome.cont()

api.on("before_tool_call", on_tool)
```

| `HookOutcome` | Effect |
|---|---|
| `HookOutcome.cont()` | Default; loop proceeds. |
| `HookOutcome.deny(reason)` | Block the action; loop synthesises a tool error. |
| `HookOutcome.modify(new_event)` | Mutate the event; continue with subsequent handlers. |
| `HookOutcome.replace(value)` | Short-circuit with a synthetic result (only meaningful on `before_tool_call`). |

The first non-`Continue` outcome wins. Extensions can also register
tools dynamically:

```python
api.register_tool(MyTool())
```

---

## Compaction

The kernel ships a `Compactor` that summarises the middle of the
transcript when token usage crosses a threshold:

```python
from pyharness import Compactor

agent = Agent(
    AgentOptions(model="claude-opus-4-7", compaction_threshold_pct=0.8),
    ...,
    compactor=Compactor(
        keep_last=10,                        # last N messages kept verbatim
        summarization_model="claude-haiku-4-5",
    ),
)
```

When triggered, the compactor:

1. Keeps system + last `keep_last` messages verbatim.
2. Sends the middle to `summarization_model` for a summary.
3. Replaces the middle with a synthetic user message
   `[compacted summary]\n<summary>`.
4. Emits a `compaction` event so resume can reconstruct correctly.

If `compactor=None`, the loop never compacts and you'll hit the
provider's context limit on long sessions.

---

## Sessions

```python
from pyharness import Session

# New session at workspace
session = Session.new(workspace)

# Resume a previous session by id
session = Session.resume(session_id)

# Fork — copy events up to a sequence number into a new session
session = Session.fork(source_session_id, fork_at_event=42)
```

Sessions live at `~/.pyharness/sessions/<cwd-hash>/<session-id>.jsonl`
(override with `PYHARNESS_SESSION_DIR`). One file per session, one
event per line, fsync'd writes.

```python
events = session.read_events()      # list[AgentEvent]
messages = session.read_messages()  # list[Message] reconstructed for LLM
```

---

## Run Result

```python
RunResult(
    session_id="...",
    final_output="...",            # last assistant text
    turn_count=3,
    cost=0.0123,                   # cumulative across run/continue calls
    files_written=[...],
    completed=True,
    reason="completed",            # "completed" | "max_turns" | "aborted" | "error"
)
```

| `reason` | Cause |
|---|---|
| `"completed"` | LLM returned no tool calls, or every tool in the final batch set `terminate=True`. |
| `"max_turns"` | Hit `AgentOptions.max_turns`. |
| `"aborted"` | `abort_event` set. |
| `"error"` | LLM raised, or a `before_llm_call` handler returned `Deny`. Resumable via `continue_run()`. |

---

## Low-Level API

For sub-agents, web embeddings, or tests that don't want the durable
session lifecycle, call the kernel directly:

```python
import asyncio
from pyharness import (
    LoopConfig, agent_loop, agent_loop_continue,
    LLMClient, ToolRegistry, UserMessageEvent,
)

config = LoopConfig(
    model="claude-opus-4-7",
    max_turns=10,
    max_tokens=None,
    tool_output_max_bytes=51_200,
    tool_output_max_lines=2_000,
    tool_timeouts={},
    tool_execution="parallel",
    model_context_window=200_000,
    compaction_threshold_pct=0.8,
    compactor=None,
    session_id="my-session",
    run_id="my-run",
    workspace=Path.cwd(),
    settings_snapshot={},
)

messages = []  # mutated in place

async def event_sink(event):
    print(event.type, getattr(event, "content", ""))

async def lifecycle(name, payload):
    from pyharness import HookOutcome
    return HookOutcome.cont()

async def empty_drain():
    return []

result = await agent_loop(
    initial_prompt="hi",
    messages=messages,
    config=config,
    tool_registry=ToolRegistry(),
    llm=LLMClient(),
    session_appender=event_sink,
    emit_lifecycle=lifecycle,
    drain_steering=empty_drain,
    drain_followup=empty_drain,
    abort_event=asyncio.Event(),
    files_written=[],
    user_message_event_factory=lambda c: UserMessageEvent(
        session_id=config.session_id, content=c
    ),
)
```

`agent_loop_continue` has the same signature minus `initial_prompt`
and `user_message_event_factory`. The kernel never touches disk —
your `event_sink` decides what's persisted and where.

---

## Public Exports

For the complete export list see
[`src/pyharness/__init__.py`](src/pyharness/__init__.py).

| Group | Symbols |
|---|---|
| Loop | `Agent`, `AgentOptions`, `AgentHandle`, `RunResult`, `agent_loop`, `agent_loop_continue`, `LoopConfig`, `LoopResult` |
| LLM | `LLMClient`, `LLMError`, `LLMResponse`, `StreamEvent`, `TokenUsage`, `count_tokens` |
| Tools | `Tool`, `ToolRegistry`, `ToolContext`, `ToolError`, `ToolResult`, `ToolExecutionResult`, `execute_tool`, `safe_path` |
| Sessions | `Session`, `SessionInfo` |
| Queues | `MessageQueue` |
| Extensions | `EventBus`, `ExtensionAPI`, `HookOutcome`, `HookResult`, `HandlerContext`, `LifecycleEvent` |
| Compaction | `Compactor`, `CompactionResult` |
| Types | `Message`, `ToolCall` |
| Events | `AgentEvent`, `SessionStartEvent`, `UserMessageEvent`, `AssistantMessageEvent`, `ToolCallStartEvent`, `ToolCallEndEvent`, `CompactionEvent`, `SteeringMessageEvent`, `FollowUpMessageEvent`, `SkillLoadedEvent`, `SessionEndEvent`, `parse_event` |

---

## What's Not Here (Deliberately)

- **No built-in tools.** Those live in [`coding-harness`](../coding-harness/).
- **No `settings.json` loader.** You pass `AgentOptions` yourself.
- **No `AGENTS.md` walking.** The system prompt is whatever you hand in.
- **No named sub-agents, skills loader, or extension file discovery.** Application concerns; see `coding-harness`.
- **No CLI.** The kernel is a library.

---

## License

MIT

---
---

# If You've Never Built One of These

Everything above is the API surface. This section is for someone
who's about to build their first agent loop and wants to understand
*why* this code is shaped the way it is.

## What is an "agent loop"?

A modern LLM is a function. You give it a list of messages, it gives
you back one assistant message. That's it. Streaming, thinking,
content blocks — implementation details. The interface is:

```
fn(messages: list[Message]) -> Message
```

This function is **stateless and one-shot**. It cannot read files,
run code, search the web, or take any action. It can only emit text
and a structured request to call a *tool*.

A tool is a function *you* expose to the model: a name, a description,
a JSON schema for its arguments. The LLM might respond with a
`tool_call` instead of (or alongside) text — "please call `read_file`
with `{path: "config.json"}`". You then **run that tool yourself**,
take the result, append it to the messages, and call the LLM again.

That second call is where the magic happens: the LLM sees the tool
result, decides what to do next, and either calls more tools or
produces a final answer.

The "agent loop" is the code that drives this back-and-forth:

```
                    ┌──────────────────────────────────────┐
                    │  messages: [system, user, ...]       │
                    └─────────────────┬────────────────────┘
                                      │
                                      v
              ┌───────────────────────────────────────────────┐
              │  call LLM (stream, accumulate, get response)  │
              └─────────────────┬─────────────────────────────┘
                                │
              ┌─────────────────┴──────────────────┐
              │                                    │
        no tool_calls                       tool_calls present
              │                                    │
              v                                    v
        ┌───────────┐                  ┌────────────────────────┐
        │ done; the │                  │ for each tool_call:    │
        │ assistant │                  │   validate args        │
        │ message   │                  │   run tool             │
        │ is final  │                  │   append tool result   │
        └───────────┘                  │     to messages        │
                                       └───────────┬────────────┘
                                                   │
                                                   v
                                            (loop back to LLM call)
```

That's the whole idea. Everything else in this codebase is
elaboration on top of these few lines.

## Why isn't it three lines of Python?

In a notebook it can be. The complications are real but they only
appear when you try to run this in production:

1. **Things go wrong.** The LLM returns malformed JSON for tool args.
   The tool times out. The network drops mid-stream. The model
   hallucinates a tool that doesn't exist. The user hits Ctrl-C
   halfway through. You need to handle every one of these without
   crashing the whole session.

2. **Things take time.** The LLM call is 3-30 seconds. Tools can be
   anywhere from milliseconds (Read) to minutes (compile, test). The
   user wants to see streaming output, intervene mid-run, and not
   stare at a frozen terminal.

3. **Things accumulate.** A long session has hundreds of messages.
   The context window fills. You have to compact (summarise old
   messages) without losing critical state.

4. **Things must persist.** A session is the durable record of an
   interaction — for replay, debugging, billing, audit. You can't
   keep it in memory; the process can die. JSONL on disk, fsync'd
   per event.

5. **Things must compose.** You'll want to ban tools (`rm -rf`),
   add observability (cost tracking), gate calls (require approval).
   That's the extension system: hooks, lifecycle events, hookable
   pre/post.

Each of these forces a design decision. The result is a few hundred
lines of code, not three.

## How the loop here fits together

Read this with one finger on `agent_loop.py` and one on `loop.py`.

```
                    ┌────────────────────────────────────────┐
                    │              Agent (loop.py)           │
                    │                                        │
                    │   owns: queues, abort, session log,    │
                    │   listener fan-out, transcript,        │
                    │   cost accumulation                    │
                    │                                        │
                    │   builds: LoopConfig + closures        │
                    │           (drain_steering, etc)        │
                    └─────────────────┬──────────────────────┘
                                      │ delegates to
                                      v
                    ┌────────────────────────────────────────┐
                    │     agent_loop() (agent_loop.py)       │
                    │                                        │
                    │   pure coroutine. takes everything as  │
                    │   arguments. doesn't know about        │
                    │   files, queues, listeners — just      │
                    │   calls the closures it was given.     │
                    └────────────────────────────────────────┘
```

`Agent` is **lifecycle**. It exists so the common case ("I want a
durable session, queues for steering, an abort I can call from a
signal handler, an event bus for extensions") is one constructor.

`agent_loop()` is **the loop**. Free coroutine. Stateless on
`self`. Takes all dependencies as parameters. Same kernel can power
a CLI, a sub-agent, a test that collects events into a list, or a
browser-side embedder that proxies to a server.

You can use the high-level `Agent` for 95% of cases. Drop to
`agent_loop()` when the standard lifecycle doesn't fit.

## A turn, in slow motion

Let's trace one turn from the user's perspective. User types:
"What's in `config.json`?". The model has access to a `read_file`
tool. Here's what happens:

```
                                  ┌──────────────────────┐
              user typing ─────►  │ steering / follow-up │
                                  │      queues          │
                                  └──────────┬───────────┘
                                             │ drained at turn top
                                             v
              ┌──────────────────────────────────────────────────────┐
              │  messages: [system, user("What's in config.json?")]  │
              └─────────────────────────┬────────────────────────────┘
                                        │
                                        │  maybe_compact (skip if under threshold)
                                        v
              ┌──────────────────────────────────────────────────────┐
              │  emit before_llm_call                                │
              │  llm.complete(model, messages, tools)                │
              │  emit after_llm_call                                 │
              └─────────────────────────┬────────────────────────────┘
                                        │
                                        v
              ┌──────────────────────────────────────────────────────┐
              │  response.tool_calls = [{                            │
              │    id: "tc1",                                        │
              │    name: "read_file",                                │
              │    arguments: {path: "config.json"}                  │
              │  }]                                                  │
              │                                                      │
              │  append assistant_message to transcript and to JSONL │
              └─────────────────────────┬────────────────────────────┘
                                        │
                                        │  ── tool batch dispatch ──
                                        │
                                        v
                       ┌────────────────────────────────────┐
                       │ phase 1: PREFLIGHT (sequential)    │
                       │  for each tool_call:               │
                       │    look up tool in registry        │
                       │    emit before_tool_call           │
                       │    if hook denied/replaced ──► immediate result
                       │    else add to runnable queue      │
                       └─────────────────┬──────────────────┘
                                         │
                                         v
                       ┌────────────────────────────────────┐
                       │ phase 2: EXECUTE                   │
                       │  if parallel + no sequential tool: │
                       │    asyncio.gather(*runnable)       │
                       │  else:                             │
                       │    for each: run, check abort/steer│
                       │  each call:                        │
                       │    validate args (Pydantic)        │
                       │    tool.execute(args, ctx)         │
                       │    truncate output if huge         │
                       │    emit tool_call_end              │
                       │    emit after_tool_call            │
                       └─────────────────┬──────────────────┘
                                         │
                                         v
                       ┌────────────────────────────────────┐
                       │ phase 3: PERSIST (source order)    │
                       │  for tc in tool_calls:             │  ◄── original order
                       │    append Message(role="tool",     │
                       │      tool_call_id=tc.id,           │
                       │      content=result.content)       │
                       └─────────────────┬──────────────────┘
                                         │
                                         v
                       ┌────────────────────────────────────┐
                       │ if all results.terminate:          │
                       │   stop now, skip next LLM call     │
                       │ if steering queue not empty:       │
                       │   drain, re-run turn               │
                       │ else: emit turn_end, loop back     │
                       └─────────────────┬──────────────────┘
                                         │
                                         v
              ┌──────────────────────────────────────────────────────┐
              │  messages: [system, user, assistant_with_tc, tool]   │
              │              ─────────────────                       │
              │              cache prefix here                       │
              │  call LLM again — it sees the tool result and        │
              │  produces the final answer.                          │
              └──────────────────────────────────────────────────────┘
```

Two things to look at twice:

**Why preflight separately?** Validation and `before_tool_call`
hooks need to run before any tool actually does work. If you ban
`bash` you want to ban it before it runs, not race against it.
Preflight is also where unknown tools and validation failures get
turned into immediate synthetic results.

**Why persist in source order, not completion order?** The next LLM
call sends the transcript as context. If parallel mode appended
tool results in completion order, two runs that issued the same
tool calls would produce two different transcripts (because
completion order varies with network jitter), which means two
different cache keys. Source order is deterministic; cache hits.

## Why a separate session log AND a separate event bus?

Two streams that look similar but solve different problems:

```
            ┌─────────────────────────┐
            │      agent_loop         │
            └─────┬───────────────┬───┘
                  │               │
                  v               v
       ┌──────────────────┐  ┌─────────────────────┐
       │  session log     │  │   event bus         │
       │  (JSONL on disk) │  │   (in-memory)       │
       │                  │  │                     │
       │  durable record. │  │  live observation.  │
       │  fsync'd writes. │  │  handlers can       │
       │  resume + fork.  │  │  Deny / Replace.    │
       └──────────────────┘  └─────────────────────┘
                  ▲                    ▲
                  │                    │
            replay / billing      extensions:
                                  - cost tracker
                                  - permission check
                                  - tool ban list
```

The session log is the **truth**: this is what happened, in order,
durably. You can crash and resume; you can fork a session at event
42 to explore a different branch; you can replay a session into a
new compactor.

The event bus is the **handle**: extensions, the TUI, observers can
react in real time. Some handlers can change behavior (`Deny` a
tool call) but those decisions are recorded in the session log too
— the bus doesn't replace persistence, it sits alongside it.

If you only had the bus, you couldn't resume after a crash. If
you only had the log, you couldn't write extensions.

## Why steering and follow-up queues?

Two distinct user intents while the agent is running:

- **"Stop, do this instead."** That's a *steer*. The user wants to
  change direction *now*. The loop drains the steering queue at
  every turn boundary (and between tool calls in sequential mode)
  and injects the message as `[steering] <content>`.
- **"When you're done with that, also do this."** That's a
  *follow-up*. The user is queuing more work. The loop drains the
  follow-up queue at turn top, alongside steering, but it's
  semantically just an append.

You need both because they're different in what they tell the
model. A steer says "the previous instruction is now stale." A
follow-up says "after the previous instruction, also do X."

## Why is the loop split into a kernel and a wrapper?

Because not every embedder wants a JSONL session on local disk.

Concrete cases that hit this:

- **Sub-agents.** A coding agent that spawns a research sub-agent
  shouldn't open a second session file. The sub-agent should share
  its parent's session — or write to a parent-controlled stream.
- **Web embedding.** Browser-side agent driving a server proxy. No
  filesystem. Events stream to the server over a socket.
- **Tests.** You want to run the loop and collect events into a
  list, no fsync, no tmpdir.

The kernel takes a `session_appender` callable. Pass
`session.append_event` to get the standard JSONL behaviour. Pass a
list's `append` method to collect events in memory. Pass a
WebSocket send to forward them to a UI. The kernel doesn't care.

## What pi-mono got right that we copied

- **Three-phase tool dispatch.** Preflight sequential, execute
  concurrent, persist in source order. The cache-determinism
  argument is theirs.
- **`terminate` signal.** Saves a turn for terminal-by-intent tools.
- **`continue_run` as a primitive.** Don't synthesise "please
  continue" prompts — they invalidate the cache.
- **Free-function loop with a stateful wrapper.** Keeps the kernel
  composable.

## What we have that pi-mono doesn't

- **Durable JSONL session log built into the wrapper.** Resume,
  fork, replay are first-class.
- **Built-in compaction.** pi-mono leaves it to the embedder via
  `transformContext`; we have a `Compactor` you can plug in.
- **Mid-tool-batch steering.** In sequential mode, the loop checks
  the steering queue *between* tool calls, not just at turn
  boundaries.
- **Explicit Anthropic `cache_control` placement.** We mark cache
  breakpoints on the system prompt and last tool definition for
  Anthropic models in `llm.py`, instead of relying on `sessionId`.

---

## See Also

- [`coding-harness`](../coding-harness/) — opinionated coding agent built on this kernel (built-in tools, settings, AGENTS.md, skills, named sub-agents, CLI)
- [`tui`](../tui/) — minimal interactive shell
- [`DESIGN.md`](../../DESIGN.md) — principles and architecture
- [docs/guides/build-finance-harness.md](../../docs/guides/build-finance-harness.md) — building a domain-specific harness
- [pi-mono](https://github.com/badlogic/pi-mono) — the TypeScript original
