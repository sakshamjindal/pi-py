# pyharness — agent SDK

The kernel: LLM client, agent loop, tool ABC, sessions, queues,
events, and the extension runtime contract. It does not know about
AGENTS.md, settings.json, named sub-agents, skills, built-in tools,
or any CLI — those concerns live in the `harness` package.

## Quick start

```python
import asyncio
from pathlib import Path

from pyharness import (
    Agent, AgentOptions, EventBus, LLMClient, Session, ToolRegistry,
)

async def main() -> None:
    options = AgentOptions(model="claude-haiku-4-5", max_turns=10)
    workspace = Path.cwd()
    session = Session.new(workspace)

    agent = Agent(
        options,
        system_prompt="You are a minimal echo agent.",
        tool_registry=ToolRegistry(),
        session=session,
        event_bus=EventBus(),
        workspace=workspace,
        llm=LLMClient(),
    )
    result = await agent.run("say hello")
    print(result.final_output)

asyncio.run(main())
```

You supply the system prompt, tools, and (optionally) a Compactor.
The loop emits lifecycle events through the `EventBus` so extensions
can deny/replace/observe LLM calls and tool invocations.

## What's in the box

- `Agent` / `AgentOptions` — the loop and its config.
- `LLMClient` — thin LiteLLM wrapper with Anthropic prompt caching.
- `Tool`, `ToolRegistry`, `ToolContext`, `execute_tool`, `safe_path`
  — the contract any tool must satisfy.
- `Session`, `SessionInfo` — JSONL event log; new/resume/fork.
- `MessageQueue`, `AgentHandle` — steering and follow-up.
- `EventBus`, `ExtensionAPI`, `HookOutcome`, `HookResult` — extension
  runtime types.
- `Compactor` — transparent context compaction.
- All event types (`SessionStartEvent`, `ToolCallEndEvent`, ...).
