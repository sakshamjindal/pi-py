# Building a finance harness on `coding-harness`

This guide walks through building **`finance-harness`** — a
domain-specific harness for trading, portfolio analysis, or research
desks — by **subclassing `coding_harness.CodingAgent`** rather than
re-implementing the assembly layer on top of `pyharness-sdk`.

`coding-harness` already provides every piece of scaffolding a
domain harness needs that isn't actually domain-specific: settings
hierarchy, AGENTS.md walking, project root discovery, named
sub-agents, on-demand skills, extension discovery from
`<scope>/.pyharness/extensions/`, and the assembly machinery that
ties them to the SDK loop. Reuse it.

You only write the bits that are *actually* domain-specific:

| Coding agent has | Finance harness adds |
| --- | --- |
| AGENTS.md (project conventions) | (reused — finance project conventions go in the same `AGENTS.md`) |
| Named sub-agents (research-analyst.md) | Named sub-agents (`market-maker.md`, `pm-rebalance.md`) at `<scope>/.pyharness/agents/` |
| Skills (market-data, etc.) | Skills (`options-greeks`, `risk-checks`) at `<scope>/.pyharness/skills/` |
| Built-in tools (read/write/edit/bash) | Domain tools (`get_quote`, `get_positions`, `place_order`) — replace the default registry |
| `~/.pyharness/settings.json` | (reused — finance fields go in the same file via a `Settings` subclass) |
| `pyharness "fix the failing tests"` | `finance-harness "rebalance to target weights"` — your own thin CLI |

Same loop, same session log format, same extension model. Only the
*domain tools, the system prompt, and the settings extras* are new
code.

---

## What `coding-harness` already gives you

Before writing any finance code, recognize what's free:

- **`WorkspaceContext`** — walks `~/.pyharness/`, `<project>/.pyharness/`,
  `<workspace>/.pyharness/` in general-first order. Discovers
  `agents/`, `skills/`, `tools/`, `extensions/` subdirs.
- **`Settings`** — JSON config with `extra="allow"`; loaded in
  personal → project → CLI override order.
- **`discover_agents`, `load_agent_definition`, `resolve_tool_list`** —
  named sub-agents from frontmatter Markdown.
- **`discover_skills`, `LoadSkillTool`, `build_skill_index`** —
  on-demand skill bundles.
- **`load_extensions`** — file-discovered extension modules with
  `register(api)` entry points.
- **`load_tools_from_module`** — dynamic Python tool module loader
  for `<scope>/.pyharness/tools/`.
- **`CodingAgent.__init__`** — assembles workspace + settings +
  session + registry + skills + system prompt + extensions + LLM +
  Compactor → `pyharness.Agent`.

Subclass it. Don't rebuild it.

---

## Step 1 — Project layout

```
finance-harness/
  pyproject.toml
  src/finance_harness/
    __init__.py
    cli.py            # `finance-harness` entry point
    runner.py         # FinanceHarness(CodingAgent) subclass
    config.py         # FinanceSettings(Settings) — typed broker / risk fields
    risk.py           # before_tool_call extension installed in _setup
    tools/
      __init__.py     # finance_registry() — the override target
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
  "pyharness",
  "coding-harness",   # the base we're subclassing
]

[project.scripts]
finance-harness = "finance_harness.cli:main"
```

Note: depends on `coding-harness`, not just `pyharness`. You inherit
the whole assembly layer.

---

## Step 2 — Domain tools

Same `pyharness.Tool` ABC that the coding tools use:

```python
# src/finance_harness/tools/market_data.py
from pydantic import BaseModel, Field
from pyharness import Tool, ToolContext, ToolError


class _GetQuoteArgs(BaseModel):
    ticker: str = Field(description="US-listed ticker, e.g. AAPL.")


class GetQuoteTool(Tool):
    name = "get_quote"
    description = "Real-time NBBO quote for a US-listed ticker."
    args_schema = _GetQuoteArgs

    async def execute(self, args, ctx: ToolContext):
        try:
            quote = await self._provider.quote(args.ticker)
        except Exception as exc:
            raise ToolError(f"quote unavailable: {exc}") from exc
        return quote.model_dump()


# src/finance_harness/tools/orders.py
from typing import Literal

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
    description = "Submit an order. Validates against risk limits."
    args_schema = _PlaceOrderArgs

    async def execute(self, args, ctx: ToolContext):
        # ... call broker API ...
        return {"order_id": ..., "status": "submitted"}
```

Bundle them:

```python
# src/finance_harness/tools/__init__.py
from pyharness import ToolRegistry
from .market_data import GetQuoteTool
from .orders import PlaceOrderTool
from .portfolio import GetPositionsTool


def all_finance_tools():
    return [GetQuoteTool(), GetPositionsTool(), PlaceOrderTool()]


def finance_registry() -> ToolRegistry:
    reg = ToolRegistry()
    for t in all_finance_tools():
        reg.register(t)
    return reg
```

---

## Step 3 — Settings subclass for typed extras

```python
# src/finance_harness/config.py
from coding_harness import Settings


class FinanceSettings(Settings):
    # adds typed fields on top of coding-harness defaults
    broker: str = "alpaca-paper"
    max_position_usd: float = 10_000.0
    max_orders_per_run: int = 5
    enable_live_orders: bool = False
    quote_timeout_seconds: int = 10
    place_order_timeout_seconds: int = 30
```

Because `Settings` already has `model_config = ConfigDict(extra="allow")`,
finance fields can also live in `~/.pyharness/settings.json`
unannounced — but typed inheritance lets your code use
`self.settings.max_position_usd` with autocomplete and validation.

---

## Step 4 — Risk-check as an extension

Cross-cutting safety belongs in extensions, not tools. Subscribe to
`before_tool_call`:

```python
# src/finance_harness/risk.py
from pyharness import ExtensionAPI, HookOutcome


def install(api: ExtensionAPI) -> None:
    api.on("before_tool_call", _gate)


async def _gate(event, ctx):
    if event.payload.get("tool_name") != "place_order":
        return HookOutcome.cont()

    args = event.payload.get("arguments") or {}
    settings = ctx.settings  # FinanceSettings via the assembly layer
    notional = args.get("qty", 0) * _last_known_price(args.get("ticker"))

    if notional > settings.max_position_usd:
        return HookOutcome.deny(
            f"order notional ${notional:.0f} exceeds limit ${settings.max_position_usd:.0f}"
        )
    if not settings.enable_live_orders:
        return HookOutcome.replace({
            "order_id": "PAPER",
            "status": "simulated",
            "reason": "enable_live_orders is false",
        })
    return HookOutcome.cont()
```

`Deny` → tool result is the deny reason; LLM sees it and adjusts.
`Replace` → returns the synthetic value as the tool result without
the real tool ever running. Perfect for paper-trading.

User extensions in `<scope>/.pyharness/extensions/<name>.py` are
loaded automatically by `coding-harness` — no extra wiring. This
file just provides the always-on risk gate that ships with
finance-harness itself.

---

## Step 5 — The harness class: ~25 lines

```python
# src/finance_harness/runner.py
from pyharness import ExtensionAPI, ToolRegistry
from coding_harness import CodingAgent, CodingAgentConfig

from .config import FinanceSettings
from .risk import install as install_risk
from .tools import finance_registry


FINANCE_PROMPT = """\
You are an LLM-driven trading agent operating under firm risk
limits and broker mandates. Use the tools to inspect market state,
retrieve positions, and place orders. Every order is subject to
pre-trade risk checks and may be denied or simulated. When a check
denies an order, read the reason and adjust before retrying.

Operating principles:
- Never place an order without first checking the current quote and
  current position.
- Prefer limit orders over market orders unless explicitly told
  otherwise.
- If risk denies an order, surface the issue rather than retrying
  the same order.
"""


class FinanceHarness(CodingAgent):
    BASE_SYSTEM_PROMPT = FINANCE_PROMPT
    _settings_class = FinanceSettings

    def _default_tool_registry(self) -> ToolRegistry:
        return finance_registry()

    def _tool_timeouts(self) -> dict[str, float]:
        return {
            "get_quote":   float(self.settings.quote_timeout_seconds),
            "place_order": float(self.settings.place_order_timeout_seconds),
        }

    def _setup(self) -> None:
        super()._setup()
        # Install the always-on risk gate after file-discovered
        # extensions have run, so a project extension can't disable
        # it accidentally.
        api = ExtensionAPI(
            bus=self.event_bus,
            registry=self.tool_registry,
            settings=self.settings,
        )
        install_risk(api)
```

That's the entire harness class. You inherited:

- Settings loading (now with `FinanceSettings` instead of `Settings`)
- Workspace + project root discovery
- Named sub-agents (`<scope>/.pyharness/agents/<name>.md`)
- Skills (`<scope>/.pyharness/skills/<name>/`)
- File-discovered extensions (`<scope>/.pyharness/extensions/`)
- Project tools (`<scope>/.pyharness/tools/`)
- Session creation / resume / fork
- Compaction
- The full `pyharness.Agent` loop

---

## Step 6 — Thin CLI

```python
# src/finance_harness/cli.py
import argparse
import asyncio
import sys
from pathlib import Path

from coding_harness import CodingAgentConfig
from .config import FinanceSettings
from .runner import FinanceHarness


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
    cli_overrides: dict = {}
    if args.enable_live:
        cli_overrides["enable_live_orders"] = True

    cfg = CodingAgentConfig(
        workspace=workspace,
        model=args.model,
        agent_name=args.agent,
        cli_overrides=cli_overrides,
    )
    result = asyncio.run(FinanceHarness(cfg).run(prompt))
    sys.stdout.write(result.final_output.rstrip() + "\n")
    return 0 if result.completed else 1
```

That's it. The full finance-harness implementation is roughly:

| File | Lines |
| --- | --- |
| `runner.py` | ~30 |
| `config.py` | ~10 |
| `risk.py` | ~25 |
| `cli.py` | ~30 |
| `tools/*.py` | however many tools you have |

Days of work, not weeks. And every file convention, every settings
override, every extension hook works the same as in coding-harness
because it *is* coding-harness.

---

## Why subclass instead of starting from `pyharness-sdk`?

You'd write — and have to keep correct — all of the following
yourself if you started from the SDK:

- A workspace context that walks scope directories.
- A settings JSON loader with the same merge order.
- An AGENTS.md walker.
- A named-agent frontmatter parser with tool resolution.
- A skill discovery + on-demand loader (with the `load_skill` tool).
- A file-discovery extension loader.
- A project-tools dynamic loader.
- An assembly layer that ties all of this together and instantiates
  `pyharness.Agent` correctly (including the `tool_timeouts`,
  `settings_snapshot`, `agent_name` plumbing).

That's most of `coding-harness`. There's no value in re-deriving it
for a finance vertical — the conventions are the same.

You'd start from the SDK directly only if your harness genuinely
**rejects** the file conventions: e.g. a remote-orchestration harness
that doesn't have a workspace at all, or a streaming harness whose
"session" is a network connection rather than a JSONL file. For
domain harnesses that look like "use a different prompt + different
tools + different guard rails", subclass.

---

## See also

- [`build-autoresearch-harness.md`](build-autoresearch-harness.md)
  — same recipe, different domain.
- [`packages/coding-harness/README.md`](../../packages/coding-harness/README.md)
  — the full assembly walkthrough you're inheriting from.
- [`packages/pyharness-sdk/README.md`](../../packages/pyharness-sdk/README.md)
  — kernel API surface, useful when you write tools and extensions.
