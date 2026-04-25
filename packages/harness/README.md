# harness — coding-agent scaffolding

Builds on `pyharness` (the SDK kernel) to provide the out-of-the-box
behaviour of the `pyharness` CLI: file conventions (AGENTS.md,
`.pyharness/` directories), settings hierarchy, named sub-agents,
skills, extensions discovery, and the eight built-in tools.

## CLI

```bash
pyharness "fix the failing tests"
pyharness --bare "task"
pyharness --agent research-analyst "what changed in the markets today?"
pyharness sessions ls
pyharness sessions replay <id>
```

## SDK (programmatic)

```python
import asyncio
from harness import CodingAgent, CodingAgentConfig

async def main() -> None:
    agent = CodingAgent(CodingAgentConfig(
        workspace="/tmp/scratch",
        model="claude-opus-4-7",
    ))
    result = await agent.run("create hello.txt with the word 'hi'")
    print(result.final_output)

asyncio.run(main())
```

`CodingAgent` reads settings, walks AGENTS.md, discovers skills,
loads extensions, builds the tool registry, and constructs a
`pyharness.Agent` to run the loop.

## What's in the box

- Built-in tools: `read`, `write`, `edit`, `bash`, `grep`, `glob`,
  `web_search`, `web_fetch`.
- `Settings` — JSON config with personal/project/CLI merge order.
- `WorkspaceContext` — AGENTS.md walking, scope discovery.
- `AgentDefinition` + `discover_agents` + `resolve_tool_list` —
  named sub-agents loaded from `<scope>/.pyharness/agents/<name>.md`.
- `SkillDefinition` + `discover_skills` + `LoadSkillTool` — on-demand
  skills loaded from `<scope>/.pyharness/skills/<name>/`.
- `load_extensions` — discovers and registers extension modules in
  `<scope>/.pyharness/extensions/`.
- `BASE_SYSTEM_PROMPT` — the default coding-agent system prompt.
