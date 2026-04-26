# Building a finance harness on pyharness-sdk

This guide walks through building **`finance-harness`** — a
domain-specific harness for trading, portfolio analysis, or research
desks. It depends only on `pyharness-sdk` (the kernel); it is *not*
built on top of `coding-harness` because finance work has different
file conventions, different tools, different guard rails, and a
different mental model from a coding agent.

The shape mirrors what `coding-harness` does for software
engineering. After working through this, the same recipe applies to
autoresearch, quant research, ops harnesses, and so on.

---

## What you're building

| Coding agent has | Finance harness has |
| --- | --- |
| AGENTS.md (project conventions) | `STRATEGY.md` (firm / desk policies) |
| Named sub-agents (research-analyst.md) | Named sub-agents (`market-maker.md`, `pm-rebalance.md`) |
| Skills (market-data, etc.) | Skills (`options-greeks`, `risk-checks`) |
| Built-in tools (read/write/edit/bash) | Domain tools (`get_quote`, `get_positions`, `place_order`) |
| `~/.pyharness/` | `~/.finance-harness/` |
| `pyharness "fix the failing tests"` | `finance-harness "rebalance to target weights"` |

Same loop, same session log format, same extension model. Only the
*conventions* and *tools* are domain-specific.

---

## Step 1 — Project layout

```
finance-harness/
  pyproject.toml
  src/finance_harness/
    __init__.py
    cli.py            # `finance-harness` entry point
    runner.py         # FinanceAgent assembly class
    config.py         # Settings: broker creds, risk limits, default model
    workspace.py      # Strategy/policy file walking
    strategies.py     # Load STRATEGY.md and named sub-agents
    risk.py           # Pre-trade risk-check extension
    tools/
      __init__.py
      market_data.py  # get_quote, get_history, get_fundamentals
      portfolio.py    # get_positions, get_pnl
      orders.py       # place_order, cancel_order
  tests/
```

`pyproject.toml`:

```toml
[project]
name = "finance-harness"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "pyharness",         # the SDK kernel
  "pydantic>=2.6",
  # broker SDK, market-data SDK, etc.
]

[project.scripts]
finance-harness = "finance_harness.cli:main"
```

Note: depends on `pyharness` (not `coding-harness`). You're building a
peer of coding-harness, not an extension of it.

---

## Step 2 — Domain tools

Tools are Pydantic-backed `pyharness.Tool` subclasses. The args
schema doubles as the LLM-facing JSON schema and as runtime
validation.

```python
# src/finance_harness/tools/market_data.py
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field
from pyharness import Tool, ToolContext, ToolError


class _GetQuoteArgs(BaseModel):
    ticker: str = Field(description="US-listed ticker, e.g. AAPL.")


class GetQuoteTool(Tool):
    name = "get_quote"
    description = "Real-time NBBO quote for a US-listed ticker."
    args_schema = _GetQuoteArgs

    async def execute(self, args, ctx: ToolContext):
        # Hit your market-data provider here.
        # On failure, raise ToolError — the loop reports `ok=False`
        # back to the LLM so it can retry or branch.
        try:
            quote = await self._provider.quote(args.ticker)
        except Exception as exc:
            raise ToolError(f"quote unavailable: {exc}") from exc
        return quote.model_dump()  # any Pydantic / dict / str works


class _PlaceOrderArgs(BaseModel):
    ticker: str
    side: Literal["buy", "sell"]
    qty: int = Field(gt=0)
    limit_price: float | None = Field(
        default=None,
        description="Omit for a market order; set for a limit order.",
    )


class PlaceOrderTool(Tool):
    name = "place_order"
    description = "Submit an order to the broker. Validates against risk limits."
    args_schema = _PlaceOrderArgs

    async def execute(self, args, ctx: ToolContext):
        # ... call the broker API ...
        return {"order_id": ..., "status": "submitted"}
```

Tools are discoverable as a list:

```python
# src/finance_harness/tools/__init__.py
from .market_data import GetQuoteTool
from .orders import PlaceOrderTool
from .portfolio import GetPositionsTool

def all_finance_tools():
    return [GetQuoteTool(), GetPositionsTool(), PlaceOrderTool()]

def finance_registry():
    from pyharness import ToolRegistry
    reg = ToolRegistry()
    for t in all_finance_tools():
        reg.register(t)
    return reg
```

---

## Step 3 — Settings + workspace

Mirror coding-harness's settings hierarchy. Personal at
`~/.finance-harness/settings.json`; project at
`<project>/.finance-harness/settings.json`; CLI overrides win last.

```python
# src/finance_harness/config.py
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field

class Settings(BaseModel):
    model_config = ConfigDict(extra="allow")

    default_model: str = "claude-opus-4-7"
    summarization_model: str = "claude-haiku-4-5"
    max_turns: int = 50

    # finance-specific
    broker: str = "alpaca-paper"
    max_position_usd: float = 10_000.0
    max_orders_per_run: int = 5
    enable_live_orders: bool = False    # default to paper

    @classmethod
    def load(cls, *, workspace: Path, cli_overrides: dict | None = None):
        # walk personal + project settings.json, merge, validate.
        ...
```

`workspace.py` is the same pattern: discover the project root by
walking up looking for `.finance-harness/`, then surface the scope
dirs in general-first order.

---

## Step 4 — Risk-check as an extension

Cross-cutting safety lives in extensions, not tools. Subscribe to
`before_tool_call` and deny dangerous orders:

```python
# src/finance_harness/risk.py
from pyharness import EventBus, ExtensionAPI, HookOutcome


def install(api: ExtensionAPI, settings) -> None:
    api.on("before_tool_call", _gate)


async def _gate(event, ctx):
    if event.payload.get("tool_name") != "place_order":
        return HookOutcome.cont()

    args = event.payload.get("arguments") or {}
    qty = args.get("qty", 0)
    notional = qty * _last_known_price(args.get("ticker"))

    settings = ctx.settings  # passed through from the assembly layer
    if notional > settings.max_position_usd:
        return HookOutcome.deny(
            f"order notional ${notional:.0f} exceeds limit "
            f"${settings.max_position_usd:.0f}"
        )
    if not settings.enable_live_orders:
        return HookOutcome.replace({
            "order_id": "PAPER",
            "status": "simulated",
            "reason": "enable_live_orders is false",
        })
    return HookOutcome.cont()
```

This hook *intercepts* the tool call before it runs:
- `Deny` → tool result is the deny reason; LLM sees it and decides
  what to do.
- `Replace` → returns the synthetic value as the tool result without
  the real tool ever running. Perfect for paper-trading mode.

User extensions in `<scope>/.finance-harness/extensions/` can
subscribe to the same events for audit, P&L tracking, kill switches
keyed off env vars, etc. — copy `coding-harness`'s
`extensions_loader.py` verbatim.

---

## Step 5 — System prompt assembly

The system prompt is just text. Compose it from your domain
conventions:

```python
BASE_SYSTEM_PROMPT = (
    "You are an LLM-driven trading agent operating under firm risk "
    "limits and broker mandates. Use the tools to inspect market "
    "state, retrieve positions, and place orders. Every order is "
    "subject to pre-trade risk checks and may be denied or "
    "simulated. When a check denies an order, read the reason and "
    "adjust before retrying.\n\n"
    "Operating principles:\n"
    "- Never place an order without first checking current quote "
    "  and current position.\n"
    "- Prefer limit orders over market orders unless explicitly "
    "  instructed otherwise.\n"
    "- If risk denies an order, do not retry the same order; "
    "  surface the issue and ask for guidance.\n"
)


def build_system_prompt(workspace_ctx, agent_def, skills) -> str:
    parts = [BASE_SYSTEM_PROMPT.strip()]

    # firm/desk policy from STRATEGY.md
    strategy = workspace_ctx.render_strategy_md()  # equiv. of AGENTS.md
    if strategy:
        parts.append(strategy)

    # named sub-agent body (optional)
    if agent_def is not None and agent_def.body.strip():
        parts.append(agent_def.body.strip())

    # available skills
    skill_index = build_skill_index(skills)
    if skill_index:
        parts.append(skill_index)

    return "\n\n".join(parts)
```

---

## Step 6 — Assembly layer

This is the glue. Mirror `coding_harness.coding_agent.CodingAgent`.

```python
# src/finance_harness/runner.py
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import uuid

from pyharness import (
    Agent, AgentHandle, AgentOptions, Compactor, EventBus,
    ExtensionAPI, LLMClient, Message, RunResult, Session, ToolRegistry,
)

from .config import Settings
from .risk import install as install_risk
from .strategies import discover_agents, load_agent_definition
from .tools import finance_registry
from .workspace import WorkspaceContext

BASE_SYSTEM_PROMPT = "..."  # see Step 5


@dataclass
class FinanceAgentConfig:
    workspace: Path
    model: str | None = None
    agent_name: str | None = None
    settings: Settings | None = None
    extra_messages: list[Message] = field(default_factory=list)


class FinanceAgent:
    def __init__(self, config: FinanceAgentConfig):
        self.config = config
        self.workspace_ctx = WorkspaceContext(workspace=config.workspace)
        self.settings = config.settings or Settings.load(
            workspace=self.workspace_ctx.workspace
        )
        self.model = config.model or self.settings.default_model
        self.event_bus = EventBus()
        self.session = Session.new(self.workspace_ctx.workspace)

        # tool registry: domain tools + any sub-agent restrictions
        self.tool_registry: ToolRegistry = self._build_tool_registry()

        # always-on cross-cutting risk checks
        api = ExtensionAPI(
            bus=self.event_bus,
            registry=self.tool_registry,
            settings=self.settings,
        )
        install_risk(api, self.settings)

        # user extensions discovered from .finance-harness/extensions/
        # ... call your loader here ...

        self.system_prompt = self._build_system_prompt()

        self.llm = LLMClient()
        self.compactor = Compactor(
            self.llm,
            summarization_model=self.settings.summarization_model,
        )

        options = AgentOptions(
            model=self.model,
            max_turns=self.settings.max_turns,
            tool_timeouts={"place_order": 30.0, "get_quote": 10.0},
            settings_snapshot=self.settings.model_dump(),
        )
        self._agent = Agent(
            options,
            system_prompt=self.system_prompt,
            tool_registry=self.tool_registry,
            session=self.session,
            event_bus=self.event_bus,
            workspace=self.workspace_ctx.workspace,
            llm=self.llm,
            compactor=self.compactor,
            run_id=uuid.uuid4().hex,
            extra_messages=self.config.extra_messages,
        )

    async def run(self, prompt: str) -> RunResult:
        return await self._agent.run(prompt)

    def start(self, prompt: str) -> AgentHandle:
        return self._agent.start(prompt)

    # ... _build_tool_registry, _build_system_prompt as in coding_harness
```

---

## Step 7 — CLI

Trivial argparse front-end. Same shape as `coding_harness.cli`:

```python
# src/finance_harness/cli.py
import argparse, asyncio, sys
from pathlib import Path

from .runner import FinanceAgent, FinanceAgentConfig


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="finance-harness")
    p.add_argument("prompt", nargs="*")
    p.add_argument("--workspace", type=Path, default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--agent", default=None)
    p.add_argument("--enable-live", action="store_true",
                   help="Disable paper-trading replacement; place real orders.")
    args = p.parse_args(argv)

    prompt = " ".join(args.prompt).strip()
    if not prompt:
        sys.stderr.write("error: no prompt provided.\n")
        return 2

    workspace = (args.workspace or Path.cwd()).resolve()
    cfg = FinanceAgentConfig(
        workspace=workspace, model=args.model, agent_name=args.agent
    )
    if args.enable_live:
        cfg.settings = (cfg.settings or load_settings()).model_copy(
            update={"enable_live_orders": True}
        )

    result = asyncio.run(FinanceAgent(cfg).run(prompt))
    sys.stdout.write(result.final_output.rstrip() + "\n")
    return 0 if result.completed else 1
```

---

## What you get for free from `pyharness-sdk`

Without writing any of this yourself:

- The full agent loop (queue draining, compaction, tool dispatch,
  steering, abort, max-turns).
- Pydantic-validated tool args with errors looped back to the LLM
  rather than crashing the run.
- Append-only JSONL session log, with resume + fork by sequence
  number. Every order ever placed is on disk.
- Anthropic prompt caching applied automatically on Claude models
  (cuts cost on long-running desks).
- Live steering via `agent.start()` + `handle.steer(...)` — perfect
  for "wait, pause that, switch to risk-off mode".
- A typed event bus that any monitoring system, audit pipeline, or
  kill-switch can hook into.

What you write: tools, file conventions, the system prompt, the
risk extension, the assembly class, the CLI. Days of work, not
weeks.

---

## See also

- [`build-autoresearch-harness.md`](build-autoresearch-harness.md)
  — same recipe, different domain.
- [`packages/pyharness-sdk/README.md`](../../packages/pyharness-sdk/README.md)
  — kernel API surface and the loop diagram.
- [`packages/coding-harness/README.md`](../../packages/coding-harness/README.md)
  — the worked example to read alongside this guide.
