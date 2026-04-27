# Building a Finance Harness on pyharness

> **This is an example recipe.** To understand the kernel concepts this
> guide builds on — workspace, named agents, skills, extensions, the
> SDK API — read [`packages/coding-harness/README.md`](../../packages/coding-harness/README.md)
> **first**. This guide *applies* those concepts to a finance domain;
> it does not re-derive them.

Pyharness is the engine. The finance harness is everything you build
around it: domain tools, AGENTS.md content, agent definitions, skills,
extensions, orchestrators, output destinations, evals.

**Pyharness doesn't know it's running finance work — it just runs whatever
AGENTS.md and tools you give it.**

Think of it like this: pyharness is the loop and the plumbing. The finance
harness is the firm's investment process expressed in files that pyharness
consumes. The finance harness is everything you build around the kernel:
tools, agents, AGENTS.md, extensions, orchestrators, eval, dashboard.

---

## The directory structure

```
/finance/
  README.md
  AGENTS.md                                    # firm-wide philosophy

  .pyharness/
    settings.json                              # firm defaults: model, thresholds
    agents/                                    # named agent definitions
      research-analyst.md
      risk-reviewer.md
      macro-watcher.md
      earnings-analyst.md
      pairs-screener.md

    tools/                                     # always-on Python tools
      __init__.py
      market_data.py                           # get_quote, get_fundamentals, get_history
      research_archive.py                      # query_research_archive, get_thesis_history
      backtester.py                            # run_backtest
      news.py                                  # get_news, search_filings
      portfolio.py                             # get_portfolio_state, get_factor_exposures
      proposals.py                             # propose_trade, flag_for_review
      risk.py                                  # calculate_var_impact, check_correlations

    skills/                                    # conditional capabilities
      options-deep-analysis/
        SKILL.md
        tools.py                               # calc_greeks, model_iv_surface
      paper-replication/
        SKILL.md
        tools.py
      synthetic-data/
        SKILL.md
        tools.py
      sector-deep-dive/
        SKILL.md

    extensions/                                # firm-specific behavior
      audit_logger.py                          # writes to firm audit pipeline
      cost_tracker.py                          # per-run cost caps
      circuit_breaker.py                       # market-halt awareness

  research/                                    # research agent's workspace
    AGENTS.md                                  # research-specific guidance
    findings/
      proposals/
        2026-04-25-aapl-services.md
        ...
      no-proposals/
    tickers/                                   # living per-ticker memory
      AAPL/
        summary.md
        thesis_history.md
        open_questions.md
      MSFT/
      ...

  risk/                                        # risk agent's workspace
    AGENTS.md
    portfolio_state/
      current.md                               # today's positions
      factor_exposures.md
    assessments/

  macro/                                       # macro agent's workspace
    AGENTS.md
    findings/

  orchestrator/                                # parent process workspace
    AGENTS.md
    daily/
      2026-04-25.md                            # daily summary
      2026-04-25-slack.md                      # what got sent to Slack

  evals/                                       # eval task definitions
    historical_situations/
      2024-01-fed-pivot.md
      2024-08-yen-carry-unwind.md
      ...
    rubric.md
    runs/                                      # eval run outputs

  workflows/                                   # parent process Python code
    morning_routine.py
    earnings_response.py
    deep_dive.py
```

Everything has a place. Domain experts (PMs, researchers, risk officers,
compliance) edit the markdown files. Engineers edit the Python in
`.pyharness/tools/` and `workflows/`.

Notice what isn't there: **no harness code.** Pyharness is installed as a
dependency, not vendored. You upgrade pyharness independently of your firm
code.

---

## Stage 1: Wire up the data

Before any agents, build the tool layer. This is where most of the
engineering effort lives — these are wrappers around your existing data
services.

```python
# /finance/.pyharness/tools/market_data.py
from pyharness import Tool, ToolContext
from pydantic import BaseModel, Field
from datetime import datetime, timezone

class _GetQuoteArgs(BaseModel):
    ticker: str = Field(description="Stock ticker, e.g. 'AAPL'")

class _Quote(BaseModel):
    ticker: str
    bid: float
    ask: float
    last: float
    volume: int
    timestamp: str
    venue: str

class GetQuoteTool(Tool):
    name = "get_quote"
    description = "Get the latest market quote for a ticker. 15-min delayed."
    args_schema = _GetQuoteArgs
    result_schema = _Quote

    async def execute(self, args: _GetQuoteArgs, ctx: ToolContext) -> _Quote:
        # Hit your firm's market data service
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"https://internal-marketdata.firm/quote/{args.ticker}",
                headers={"Authorization": f"Bearer {ctx.settings.marketdata_token}"},
            )
            data = r.json()
            return _Quote(**data)

# Repeat for: get_fundamentals, get_history, get_options_chain, etc.

# Module exports the list of tools
TOOLS = [
    GetQuoteTool(),
    GetFundamentalsTool(),
    GetHistoryTool(),
]
```

The pyharness convention: each tool module has a `TOOLS = [...]` list at
module level. The harness, when an agent declares `tools: [get_quote, ...]`,
walks the `.pyharness/tools/` modules, finds the matching tool implementations,
and registers them.

You'd write maybe 30-50 of these, organized by domain:

- **Market data:** `get_quote`, `get_fundamentals`, `get_history`,
  `get_options_chain`, `get_volatility_surface`
- **News and filings:** `search_news`, `get_filing`, `list_recent_filings`,
  `get_earnings_transcript`
- **Research archive:** `query_research_archive`, `get_thesis_history`,
  `list_recent_research`
- **Compute:** `run_backtest`, `calculate_factor_exposures`, `calculate_var`,
  `calculate_correlation`
- **Portfolio:** `get_portfolio_state`, `get_position_history`,
  `get_factor_exposures`
- **Proposals:** `propose_trade`, `flag_for_review`, `submit_research_note`
- **Risk:** `calculate_var_impact`, `check_concentration`, `check_factor_limits`
- **Sandbox:** `python_sandbox` (E2B or Modal wrapper)

Each is a thin wrapper around your existing infrastructure. If your firm
doesn't have these services yet, building them is the real project —
pyharness is just the consumer.

This phase is 4-8 weeks for a small team. It's not glamorous, but it's
where the actual finance value lives.

---

## Stage 2: Write the firm-wide AGENTS.md

This is where your firm's investment philosophy gets encoded. It's the
document senior leadership and PMs care about most. Treat it like the
firm's articles of incorporation, but for AI agents.

```markdown
# /finance/AGENTS.md

# Firm

We are a long/short equity hedge fund focused on US large and mid-cap names.
AUM: $300M. Investment style: fundamentals-driven with disciplined risk
management. Target: market-neutral with positive alpha through stock selection.

## Investment Philosophy

### What we believe
- Markets are inefficient on horizons of weeks to months
- Fundamentals drive returns over our 3-12 month holding period
- Risk-adjusted returns matter more than absolute returns
- Position sizing is as important as idea selection

### What we don't do
- We don't trade derivatives except for hedging existing equity positions
- We don't use leverage above 1.5x gross
- We don't trade earnings announcements (binary events, low edge)
- We don't take political views on macro

## Style

- **Skeptical by default.** Prove the thesis. Assume nothing.
- **Always state what would change your mind.** A thesis without
  invalidation criteria is faith, not analysis.
- **Cite specific data points.** Never vague claims like "growing fast" —
  always with numbers and periods.
- **Acknowledge counterarguments.** Every long has a bear case worth
  understanding.
- **Size on conviction x payout, not just conviction.**

## Constraints (hard rules)

- Position size: max 2% of NAV per name
- Sector concentration: max 25% of NAV
- Gross leverage: max 1.5x
- Restricted list: see /finance/.pyharness/restricted_tickers.txt
- No material non-public information ever
- Earnings blackout: no trades 24h before/after earnings on names you hold
```

This is firm IP. It's also exactly what new analysts at the firm would
read on day one. The fact that AI agents read it too is a feature — it
forces the firm to articulate what it actually believes, in writing, where
everyone can see and edit it.

This document evolves. Every retrospective on what you got wrong adds a
paragraph. Every successful pattern that wasn't documented gets written
down. Six months in, AGENTS.md is the firm's accumulated wisdom in
markdown form.

---

## Stage 3: Write the agent definitions

For each role, a markdown file with frontmatter declaring identity and
tools, and a body declaring workflow.

```markdown
# /finance/.pyharness/agents/research-analyst.md

---
name: research-analyst
description: Generates fundamental trade proposals on US equities
model: claude-opus-4-7
tools:
  - read
  - write
  - edit
  - grep
  - glob
  - web_search
  - web_fetch
  - get_quote
  - get_fundamentals
  - get_history
  - search_news
  - get_filing
  - get_earnings_transcript
  - query_research_archive
  - get_thesis_history
  - run_backtest
  - calculate_factor_exposures
  - python_sandbox
  - propose_trade
  - flag_for_review
workdir: /finance/research
---

# Research Analyst

You are a research analyst at a long/short equity hedge fund. Your job is
to generate well-reasoned trade proposals grounded in fundamentals.

You DO NOT execute trades. You produce TradeProposal documents that humans
review and (sometimes) execute manually.

## Workflow for ticker-specific tasks

When asked to research a specific name:

1. **Check prior work.** Read /finance/research/tickers/{TICKER}/summary.md
   if it exists.

2. **Gather context.**
   - Fundamentals: get_fundamentals for last 4 quarters
   - Recent news: search_news for the past 30 days
   - Recent filings: get_filing for any 8-K, 10-Q since last summary
   - Macro context: read /finance/macro/findings/{today}.md if it exists

3. **Form hypothesis.** State your view explicitly.

4. **Validate.** Use python_sandbox for any custom analysis. Run a
   backtest if appropriate.

5. **Counterarguments.** What could go wrong? What does the market see
   that you don't?

6. **Decide.** If high conviction, write a proposal. If not, write a
   "no-proposal" file explaining why. Do not manufacture conviction.

## Constraints

- Never propose more than 2% NAV per name
- Never propose during earnings blackout
- Always include a stop-loss
- Always include invalidation criteria
```

That's one agent. Risk-reviewer, macro-watcher, earnings-analyst,
pairs-screener follow the same pattern with different identity and tools.

The agent definitions are where the firm's process gets translated into
executable form. A PM reviewing `research-analyst.md` should read it
and say "yes, that's how we do research." If they disagree, they edit the
file. The agent's behavior changes accordingly.

---

## Stage 4: Write the orchestration code

The parent process that drives the daily workflow. This is plain Python;
pyharness is just a library.

```python
# /finance/workflows/morning_routine.py
import asyncio
from datetime import date
from pathlib import Path

from coding_harness import CodingAgent, CodingAgentConfig

import structlog

log = structlog.get_logger()


async def run_agent(agent_name: str, prompt: str, **overrides) -> dict:
    """Run a named agent and return structured result."""
    config = CodingAgentConfig(
        workspace=Path("/finance"),
        agent_name=agent_name,
        **overrides,
    )
    agent = CodingAgent(config)
    result = await agent.run(prompt)
    return result


async def morning_workflow():
    """Daily morning research workflow.

    Sequence:
    1. Macro brief (depends on nothing)
    2. Research screen (depends on macro brief)
    3. Risk reviews (depend on research, parallel per proposal)
    4. Notify humans about approved proposals
    """
    today = date.today().isoformat()
    log.info("morning_workflow_start", date=today)

    # Step 1: Macro brief
    log.info("running_macro")
    await run_agent(
        "macro-watcher",
        f"Generate today's macro brief for {today}. "
        f"Focus on US session prep, rate expectations, and any overnight moves "
        f"affecting our portfolio."
    )

    macro_brief_path = f"/finance/macro/findings/{today}.md"
    if not Path(macro_brief_path).exists():
        log.error("macro_brief_missing", path=macro_brief_path)
        return

    # Step 2: Research generates proposals
    log.info("running_research")
    await run_agent(
        "research-analyst",
        f"Daily research run for {today}. "
        f"Reference today's macro brief at {macro_brief_path}. "
        f"Run a setup screen on the firm watchlist and produce 1-3 high-conviction "
        f"proposals. If no high-conviction setups exist today, write a 'no-proposal' "
        f"file explaining why."
    )

    proposals_dir = Path("/finance/research/findings/proposals")
    proposals = sorted(proposals_dir.glob(f"{today}-*.md"))

    if not proposals:
        log.info("no_proposals_today")
        return

    log.info("proposals_generated", count=len(proposals))

    # Step 3: Risk reviews each proposal in parallel
    log.info("running_risk_reviews")
    risk_tasks = [
        run_agent(
            "risk-reviewer",
            f"Review proposal at {p}. "
            f"Read /finance/risk/portfolio_state/current.md for current positions. "
            f"Write assessment to /finance/risk/assessments/{p.stem}.md. "
            f"Begin assessment with one of: APPROVED, APPROVED_WITH_REDUCTION, "
            f"REJECTED, ESCALATE.",
        )
        for p in proposals
    ]
    risk_results = await asyncio.gather(*risk_tasks, return_exceptions=True)

    # Step 4: Process results
    for proposal, risk_result in zip(proposals, risk_results):
        if isinstance(risk_result, Exception):
            log.error("risk_failed", proposal=proposal.stem, error=str(risk_result))
            continue

        assessment_path = Path(f"/finance/risk/assessments/{proposal.stem}.md")
        if assessment_path.exists():
            verdict = assessment_path.read_text().split("\n", 1)[0].strip()
            log.info("proposal_assessed", proposal=proposal.stem, verdict=verdict)

    log.info("morning_workflow_complete")


if __name__ == "__main__":
    asyncio.run(morning_workflow())
```

This script gets scheduled via Prefect, Dagster, cron, or whatever you use.
Runs every morning at 8am. Drives the agents, watches files, notifies humans.
The entire firm process expressed in ~100 lines of Python.

---

## Stage 5: Write the extensions

These are firm-specific behaviors that wrap the harness. Drop them in
`/finance/.pyharness/extensions/` and pyharness loads them automatically.

```python
# /finance/.pyharness/extensions/audit_logger.py
"""Routes all agent events to the firm's audit pipeline."""

import json
import time
from pathlib import Path

from pyharness import ExtensionAPI, HookOutcome

_AUDIT_FILE = "audit.jsonl"


def register(api: ExtensionAPI) -> None:
    api.on("before_tool_call", _log_start)
    api.on("after_tool_call", _log_end)


async def _log_start(event, ctx):
    _write(ctx, {
        "timestamp": time.time(),
        "event": "tool_call_start",
        "tool": event.payload.get("tool_name"),
        "arguments": event.payload.get("arguments"),
    })
    return HookOutcome.cont()


async def _log_end(event, ctx):
    _write(ctx, {
        "timestamp": time.time(),
        "event": "tool_call_end",
        "tool": event.payload.get("tool_name"),
        "ok": event.payload.get("ok"),
    })
    return HookOutcome.cont()


def _write(ctx, record: dict) -> None:
    try:
        path = Path(ctx.workspace) / ".pyharness" / _AUDIT_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except OSError:
        pass
```

```python
# /finance/.pyharness/extensions/circuit_breaker.py
"""Stops agents when markets are halted or risk has revoked permission."""

import os
from pyharness import ExtensionAPI, HookOutcome


def register(api: ExtensionAPI) -> None:
    api.on("before_llm_call", _check)


async def _check(event, ctx):
    if os.path.exists("/finance/.kill_switch"):
        reason = open("/finance/.kill_switch").read().strip()
        return HookOutcome.deny(f"Kill switch active: {reason}")
    if os.environ.get("PYHARNESS_KILL_SWITCH"):
        return HookOutcome.deny("PYHARNESS_KILL_SWITCH is set")
    return HookOutcome.cont()
```

```python
# /finance/.pyharness/extensions/cost_tracker.py
"""Per-run cost cap. Aborts if cost exceeds budget."""

from pyharness import ExtensionAPI, HookOutcome

_DEFAULT_BUDGET = 5.00
_state = {"cost": 0.0}


def register(api: ExtensionAPI) -> None:
    budget = api.settings.get("per_run_budget", _DEFAULT_BUDGET) if api.settings else _DEFAULT_BUDGET

    async def _reset(event, ctx):
        _state["cost"] = 0.0
        return HookOutcome.cont()

    async def _track(event, ctx):
        response = event.payload.get("response") or {}
        usage = response.get("usage") or {}
        _state["cost"] += usage.get("cost_usd", 0.0)
        if _state["cost"] > budget:
            return HookOutcome.deny(f"Cost cap exceeded: ${_state['cost']:.2f} > ${budget:.2f}")
        return HookOutcome.cont()

    api.on("session_start", _reset)
    api.on("after_llm_call", _track)
```

These three extensions — audit logging, circuit breaker, cost tracking —
together make the harness production-ready for hedge fund use. Each is
30-80 lines. None requires changes to pyharness core.

---

## Stage 6: Build the eval harness

Before scaling, build evaluation.

```python
# /finance/evals/run_eval.py
import asyncio
import json
from pathlib import Path

from coding_harness import CodingAgent, CodingAgentConfig

EVAL_DIR = Path("/finance/evals")


async def run_eval(situation_path: Path, agent: str, model: str) -> dict:
    config = CodingAgentConfig(
        workspace=Path("/finance"),
        agent_name=agent,
        model=model,
    )
    agent_instance = CodingAgent(config)
    result = await agent_instance.run(
        f"You are operating as of the situation described in {situation_path}. "
        f"Based only on information available at that time, what would you propose?"
    )
    return {
        "situation": situation_path.name,
        "agent": agent,
        "model": model,
        "proposal": result.final_output,
        "cost": result.cost,
    }


async def run_full_eval_suite():
    situations = list((EVAL_DIR / "historical_situations").glob("*.md"))
    models = ["claude-opus-4-7", "openrouter/openai/gpt-5"]

    tasks = [run_eval(s, "research-analyst", m) for s in situations for m in models]
    results = await asyncio.gather(*tasks)

    output_dir = EVAL_DIR / "runs" / "latest"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "results.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(run_full_eval_suite())
```

Run this weekly. When you change AGENTS.md, run it again. When a new model
comes out, run it again.

---

## Stage 7: Wire up the feedback loop

Every proposal that becomes a trade has an outcome. Capture it.

```python
# /finance/feedback/capture.py
from datetime import date, timedelta
from pathlib import Path


def capture_outcomes():
    today = date.today()
    cutoff = today - timedelta(days=30)
    proposals = list(Path("/finance/research/findings/proposals").glob("*.md"))

    for proposal in proposals:
        proposal_date = _parse_date_from_filename(proposal)
        if proposal_date < cutoff:
            continue
        trade = _lookup_trade_for_proposal(proposal.stem)  # your OMS lookup
        if not trade:
            continue
        outcome = _compute_pnl(trade)
        outcome_path = (
            Path("/finance/research/tickers") / trade.ticker / "trade_outcomes" /
            f"{proposal.stem}.md"
        )
        outcome_path.parent.mkdir(parents=True, exist_ok=True)
        outcome_path.write_text(_format_outcome(proposal, trade, outcome))
```

Six months in, you have hundreds of (proposal, trade, outcome) tuples.
This is your eval data, your performance attribution data, and what lets
you say "this agent's proposals have produced X bps of alpha."

---

## Stage 8: Build the dashboard

A web UI for humans. Not a chat UI — an operations UI.

**What it shows:**
- Today's proposals queue (pending review)
- Approved / rejected / executed status per proposal
- Open positions with linked theses
- Recent agent runs with cost and outcome
- Eval leaderboard
- Audit log search

This is a Next.js + Postgres app that reads pyharness session JSONLs and
the proposal/assessment markdown files. The agents don't know it exists;
the dashboard is just a viewer over their outputs.

---

## What changes in pyharness for finance

Honest answer: **very little.** Pyharness is designed to be domain-agnostic.
The finance harness is built almost entirely *around* pyharness, not by
modifying it.

The only pyharness-level customization you need:

1. **Tool modules.** Your `.pyharness/tools/` directory has the finance
   tools. Pyharness already supports this via the `tools/` discovery
   convention.

2. **Settings.** Per-firm `settings.json` with model defaults, cost caps,
   audit endpoints. `extra="allow"` on the Settings model means
   finance-specific keys work without code changes.

3. **Extensions.** Audit logging, circuit breaker, cost tracking. Pyharness's
   extension API supports this.

Pyharness is the engine. The finance harness is everything you build
around it.

---

## The build order

**Months 1-2:** Pyharness itself. Dogfood on coding tasks.

**Months 2-4:** The tool layer. Wrap your firm's data services. ~30 tools.

**Month 3:** First agent end-to-end. Probably research-analyst.

**Month 4:** Second and third agents. Risk-reviewer and macro-watcher.
Wire up `morning_routine.py`.

**Month 5:** Extensions. Production hardening.

**Month 5-6:** Eval suite.

**Month 6:** Dashboard.

**Month 6+:** Feedback loop and continuous improvement.

---

## The summary view

A finance harness built on pyharness is:

1. The **pyharness library** (the engine)
2. **30-50 finance-specific tools** (wrappers around firm infrastructure)
3. **5-10 named agents** (markdown definitions for each role)
4. A handful of **skills** (conditional capabilities)
5. **3-5 extensions** (audit, cost, circuit breaker)
6. The **orchestration code** (Python parent processes wiring agents together)
7. **AGENTS.md hierarchy** (firm philosophy, accumulated wisdom)
8. An **eval suite** (historical situations + scoring rubric)
9. A **feedback loop** (proposal -> trade -> outcome -> retrospective)
10. A **dashboard** (operations UI for humans)

Pyharness is maybe 5% of the total system by volume but 80% of the "agent
harness" by responsibility. Everything else is your firm's IP, expressed in
a form pyharness can run.

---

## See also

- [`build-autoresearch-harness.md`](build-autoresearch-harness.md)
  — same pattern, different domain.
- [`packages/coding-harness/README.md`](../../packages/coding-harness/README.md)
  — the assembly layer you're using.
- [`packages/pyharness-sdk/README.md`](../../packages/pyharness-sdk/README.md)
  — kernel API for the tools and extensions you write.
