# Orchestration patterns

`coding-harness` deliberately ships **no** `Pipeline`, `FanOut`, or
DAG framework. Orchestration patterns are domain-shaped: a research
pipeline wants different sequencing than a code-review fan-out, and
any abstraction we built would be wrong for half of you.

What you get instead:

1. `CodingAgent` as a clean SDK class — embed it in plain Python.
2. Per-agent isolation via separate workspaces.
3. One small helper, `agent_workspace()`, for the boring filesystem
   work every recipe needs.

The `examples/orchestration/` directory holds runnable reference
recipes you can copy and adapt.

## The unit of isolation: per-agent workspace

Each `CodingAgent` instance owns one workspace directory. Multi-agent
recipes give each agent its **own** workspace and explicitly hand off
artefacts through a shared directory the orchestrator owns.

```
sandbox/
├── artefacts/              ← orchestrator owns; explicit handoff dir
└── <agent-name>/           ← each agent's workspace, isolated
```

Agents can step on their own files all they want. The orchestrator
controls what crosses agent boundaries by curating `artefacts/`.

## The helper

```python
from coding_harness import agent_workspace

async with agent_workspace(base, "research", cleanup=False) as ws:
    agent = CodingAgent(CodingAgentConfig(workspace=ws))
    await agent.run(...)
```

`cleanup=True` removes the directory on exit (good for ephemeral
per-request workspaces in a server). Default is `False` so artefacts
persist for downstream agents.

## Pattern A — Sequential pipeline

Stage 1 produces an artefact, stage 2 consumes it.

See [`examples/orchestration/pipeline.py`](../../examples/orchestration/pipeline.py)
for a runnable version. Sketch:

```python
from coding_harness import CodingAgent, CodingAgentConfig, agent_workspace

async def pipeline(base, topic):
    artefacts = base / "artefacts"
    artefacts.mkdir(parents=True, exist_ok=True)

    async with agent_workspace(base, "research") as ws:
        await CodingAgent(CodingAgentConfig(workspace=ws)).run(
            f"Research {topic}. Write findings to {artefacts}/findings.md."
        )

    async with agent_workspace(base, "writer") as ws:
        result = await CodingAgent(CodingAgentConfig(workspace=ws)).run(
            f"Read {artefacts}/findings.md and write a 500-word report."
        )
    return result.final_output
```

## Pattern B — Fan-out / fan-in

N agents in parallel, then reduce.

See [`examples/orchestration/fanout.py`](../../examples/orchestration/fanout.py).
Sketch:

```python
import asyncio

async def run_one(base, idx, prompt):
    async with agent_workspace(base, f"worker-{idx}", cleanup=True) as ws:
        agent = CodingAgent(CodingAgentConfig(workspace=ws))
        return (await agent.run(prompt)).final_output

results = await asyncio.gather(*(
    run_one(base, i, p) for i, p in enumerate(prompts)
))
```

## Pattern C — Supervisor with specialists

A supervisor agent decides which specialist to call. **Specialists are
subprocesses, not in-loop subagents** (see `DESIGN.md` principle 8).

See [`examples/orchestration/supervisor.py`](../../examples/orchestration/supervisor.py).

```python
import subprocess

def call_specialist(role, workspace, prompt):
    return subprocess.run(
        ["pyharness", "--agent", role, "--workspace", str(workspace), prompt],
        capture_output=True, text=True, check=False,
    ).stdout
```

If you want the supervisor agent itself to invoke specialists, ship
a custom `SpawnAgentTool` whose `execute()` runs `pyharness` as a
subprocess. The supervisor's frontmatter pins it as an always-on
non-builtin tool.

## Pattern D — Resume / replay

`Session.resume(session_id)` and `Session.fork(session_id, fork_at_event=N)`
let an orchestrator checkpoint and re-run from a specific event. No
new code needed; pass `resume_from=` or `fork_from=` in
`CodingAgentConfig`.

## Sandboxing

The agent runs as a process. Pyharness intentionally does not abstract
sandbox providers. Choose the isolation that matches your deployment:

- **Local dev:** workspace dirs + per-agent processes. The default.
- **Per-tenant:** spawn each `pyharness` invocation in a Docker
  container or gVisor sandbox; pass `--workspace` to mount a dedicated
  directory.
- **Cloud functions:** ephemeral tempdir per invocation; nothing
  persists.

The agent doesn't change between these — only the deployment shell
changes.
