# Building a Finance Harness on pyharness

Pyharness is the engine. The finance harness is everything you build
around it: domain tools, AGENTS.md content, agent definitions, skills,
extensions, orchestrators, output destinations, evals.

**Pyharness doesn't know it's running finance work — it just runs whatever
AGENTS.md and tools you give it.**

Think of it like this: pyharness is the loop and the plumbing. The finance
harness is the firm's investment process expressed in files that pyharness
consumes. Pyharness stays at ~1500 lines forever. The finance harness is
everything you build around it: tools, agents, AGENTS.md, extensions,
orchestrators, eval, dashboard.

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

## How we think about edges

We believe our edges are:
1. Time horizon arbitrage — we hold longer than most quant systems, shorter
   than most index funds
2. Fundamental depth — we read 10-Ks and listen to calls; many shops don't
3. Sector specialization — we go deeper than generalists in tech, financials,
   industrials
4. Risk discipline — we cut losers fast and let winners run

We do NOT have:
- Speed advantages (don't compete on latency)
- Information advantages (we use only public data)
- Quantitative breadth advantages (we cover ~150 names, not 5000)

When proposing trades, ask: which of our edges does this trade rely on? If
the answer is "none," reconsider.

## Common failure modes to avoid

- **Anchoring on initial thesis.** When new information arrives, update.
- **Confirmation bias on existing holdings.** Test current holdings as
  hard as new ideas.
- **Overweighting narrative over numbers.** A great story with bad
  fundamentals is still bad.
- **Underweighting position sizing.** Half-conviction at full size loses
  more than full-conviction at half size.

## Citations and sources

When making claims, cite:
- For fundamentals: 10-K, 10-Q, 8-K with date
- For news: source publication and date
- For our prior research: file path in /finance/research/

Never say "I read somewhere" or "it's commonly known." Find the source
or don't make the claim.
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
   if it exists. Note the current thesis (if any) and recent updates.

2. **Gather context.**
   - Fundamentals: get_fundamentals for last 4 quarters
   - Recent news: search_news for the past 30 days
   - Recent filings: get_filing for any 8-K, 10-Q since last summary
   - Macro context: read /finance/macro/findings/{today}.md if it exists

3. **Form hypothesis.** State your view explicitly. What is the thesis?
   What's the catalyst? What's the timeframe?

4. **Validate.** Use python_sandbox for any custom analysis. Run a
   backtest if appropriate. Check that the data supports the thesis.

5. **Counterarguments.** Spend at least 5 minutes on the bear case. What
   could go wrong? What does the market see that you don't?

6. **Decide.** If you have high conviction, write a proposal. If not,
   write a "no-proposal" file explaining why and stop. Do not manufacture
   conviction.

## Output format

Write proposals to findings/proposals/{YYYY-MM-DD}-{ticker}-{slug}.md.

Each proposal contains:

    {Long/Short} {TICKER} — {one-line thesis}

    ## Thesis
    One paragraph stating the bet.

    ## Supporting evidence
    3-5 bullets, each with specific data and citations.

    ## Counterarguments
    3-5 bullets, the strongest cases against this trade.

    ## Position sizing
    - Recommended size: X% NAV
    - Rationale based on conviction x payout
    - Stop-loss: $X.XX (-Y%)

    ## Catalyst and timeframe
    - Catalyst: {what makes this work}
    - Timeframe: {weeks/months}

    ## What would invalidate this thesis
    3-5 specific things; if any happen, exit immediately.

    ## Citations
    - [10-Q, Q4 2025]
    - [Earnings call transcript, 2026-01-25]
    - [Our prior thesis: /finance/research/tickers/AAPL/thesis_history.md]

## Constraints

- Never propose more than 2% NAV per name (firm constraint)
- Never propose during earnings blackout (1 day before/after earnings)
- Never propose names on the restricted list
- Always include a stop-loss
- Always include invalidation criteria

## When to flag for review (instead of proposing)

If you encounter:
- Proposed trade > 2% NAV (firm constraint, escalate)
- Material non-public information indicators (immediately flag)
- Highly unusual situation requiring human judgment
- Disagreement between data sources you can't resolve

Use flag_for_review instead of propose_trade.

## Skills available

You have these skills available to load on demand:
- options-deep-analysis: When the proposal involves options or volatility
- paper-replication: When you want to test a methodology from a paper
- synthetic-data: When historical data is incomplete
- sector-deep-dive: For comprehensive sector analysis

Load via load_skill only when relevant. Don't load by default.
```

That's one agent. Risk-reviewer, macro-watcher, earnings-analyst,
pairs-screener follow the same pattern with different identity and tools.

The agent definitions are where the firm's process gets translated into
executable form. A PM reviewing the `research-analyst.md` should read it
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

These are firm-specific behaviors that wrap the harness.

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
    api.on("session_start", _log_session_start)
    api.on("session_end", _log_session_end)


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
        "duration_ms": event.payload.get("duration_ms"),
    })
    return HookOutcome.cont()


async def _log_session_start(event, ctx):
    _write(ctx, {
        "timestamp": time.time(),
        "event": "session_start",
        "session_id": ctx.session_id,
    })
    return HookOutcome.cont()


async def _log_session_end(event, ctx):
    _write(ctx, {
        "timestamp": time.time(),
        "event": "session_end",
        "session_id": ctx.session_id,
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
"""Stops agents from running when markets are halted or risk has
revoked permission."""

import os

from pyharness import ExtensionAPI, HookOutcome


def register(api: ExtensionAPI) -> None:
    api.on("before_llm_call", _check)


async def _check(event, ctx):
    # Check the firm-wide kill switch file
    if os.path.exists("/finance/.kill_switch"):
        reason = open("/finance/.kill_switch").read().strip()
        return HookOutcome.deny(f"Kill switch active: {reason}")

    # Check environment variable
    if os.environ.get("PYHARNESS_KILL_SWITCH"):
        return HookOutcome.deny("PYHARNESS_KILL_SWITCH is set")

    return HookOutcome.cont()
```

```python
# /finance/.pyharness/extensions/cost_tracker.py
"""Per-run cost cap. Aborts if cost exceeds budget."""

from pyharness import ExtensionAPI, HookOutcome

_DEFAULT_BUDGET = 5.00  # USD per run
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
            return HookOutcome.deny(
                f"Cost cap exceeded: ${_state['cost']:.2f} > ${budget:.2f}"
            )
        return HookOutcome.cont()

    api.on("session_start", _reset)
    api.on("after_llm_call", _track)
```

These three extensions — audit logging, circuit breaker, cost tracking —
together make the harness production-ready for hedge fund use. Each is
30-80 lines. None requires changes to pyharness core.

---

## Stage 6: Build the eval harness

Before scaling, build evaluation. This is what tells you whether your
agents are actually getting better over time.

```
/finance/evals/
  rubric.md                                   # how we score
  historical_situations/                       # the test set
    2024-01-fed-pivot.md                      # known situation, known outcome
    2024-08-yen-carry-unwind.md
    2025-04-tariffs.md
    ...
  runs/                                        # eval run outputs
    2026-04-25/
      research-analyst-claude-opus-4-7/
        results.json
```

Each historical situation is a markdown file describing the state of the
world at a point in time, with the actual outcome that followed. The eval
task: given the world as of the situation date, what would the agent have
proposed?

```python
# /finance/evals/run_eval.py
import asyncio
import json
from pathlib import Path

from coding_harness import CodingAgent, CodingAgentConfig

EVAL_DIR = Path("/finance/evals")


async def run_eval(situation_path: Path, agent: str, model: str) -> dict:
    """Run a single eval task."""
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
    models = [
        "claude-opus-4-7",
        "openrouter/openai/gpt-5",
        "openrouter/google/gemini-3-pro",
    ]

    tasks = [
        run_eval(s, "research-analyst", m)
        for s in situations
        for m in models
    ]
    results = await asyncio.gather(*tasks)

    output_dir = EVAL_DIR / "runs" / "latest"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "results.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(run_full_eval_suite())
```

Run this weekly. When you change AGENTS.md, run it again. When a new model
comes out, run it again. The leaderboard tells you if changes are
net-positive or net-negative.

This is the most important piece of the finance harness and the one most
teams skip. Don't skip it.

---

## Stage 7: Wire up the feedback loop

Every proposal that becomes a trade has an outcome. Capture it.

```python
# /finance/feedback/capture.py
"""Links proposals to trade decisions and outcomes."""

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

        # Did this become a trade?
        trade = _lookup_trade_for_proposal(proposal.stem)  # your OMS lookup
        if not trade:
            continue

        # What's the current P&L?
        outcome = _compute_pnl(trade)

        # Write the outcome back to the research archive
        outcome_path = (
            Path("/finance/research/tickers") / trade.ticker / "trade_outcomes" /
            f"{proposal.stem}.md"
        )
        outcome_path.parent.mkdir(parents=True, exist_ok=True)
        outcome_path.write_text(_format_outcome(proposal, trade, outcome))
```

Six months in, you have hundreds of (proposal, trade, outcome) tuples
linked together. This is your training data, your eval data, and your
performance attribution data. It's also what lets you say "this agent's
proposals have produced X bps of alpha over Y trades, with a Z hit rate."

Without this, you have agents producing output and no idea if it's any
good.

---

## Stage 8: Build the dashboard

A web UI for humans to interact with the system. Not a chat UI — an
operations UI.

**What it shows:**
- Today's proposals queue (pending review)
- Approved / rejected / executed status per proposal
- Open positions with linked theses
- Recent agent runs with cost and outcome
- Eval leaderboard
- Audit log search

**What it lets users do:**
- Approve / reject / modify proposals
- Mark proposals as executed (manual link to OMS)
- Search past sessions
- View any session's full transcript
- Trigger ad-hoc agent runs

This is a Next.js + Postgres app that reads pyharness session JSONLs and
the proposal/assessment markdown files. The agents don't know it exists;
the dashboard is just a viewer over their outputs.

---

## What this looks like in operation

A typical day:

**8:00 AM:** Cron fires `morning_routine.py`. Macro agent runs. Research
agent runs, produces 3 proposals. Risk agents run in parallel on each
proposal.

**8:35 AM:** A PM opens the dashboard, sees 2 approved proposals, 1
rejected. Reads the approved ones, reads the risk assessments. Decides to
take 1 of the 2 trades. Marks it as executed. Submits the trade in the OMS
manually.

**Throughout the day:** An earnings-response watcher runs after each
portfolio name reports. If it sees a meaningful divergence, it writes an
alert. PM gets a Slack ping.

**3:00 PM:** A PM has a half-formed idea about NVDA. Types
`pyharness --agent research-analyst --workspace /finance/research "deep dive on NVDA, focus on the new chip cycle"`.
Agent runs for ~45 minutes, reads prior research, runs backtests, produces
a proposal. PM reads it, decides to follow up tomorrow.

**Overnight:** A retrospective job runs on closed positions from the past
week, asks the agent to evaluate "what we got right, what we got wrong,"
writes the result to `/finance/research/retrospectives/`.

**Weekly:** The eval suite runs. Leaderboard updated. If any model
regressed significantly on the historical task suite, alert.

**Quarterly:** The firm reviews AGENTS.md based on accumulated
retrospectives. Updates the philosophy. Commits to git. Agents pick up the
new philosophy automatically next run.

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
   audit endpoints. Pyharness already supports settings hierarchy.
   `extra="allow"` on the Settings model means finance-specific keys like
   `per_run_budget` or `marketdata_token` work without code changes.

3. **Extensions.** Audit logging, circuit breaker, cost tracking. Pyharness's
   extension API supports this.

Pyharness stays at ~1500 lines forever. The finance harness is everything
you build around it: tools, agents, AGENTS.md, extensions, orchestrators,
eval, dashboard.

---

## The build order

If you're building this from scratch:

**Months 1-2:** Pyharness itself. Build the harness per the spec. Dogfood
it on coding tasks while building.

**Months 2-4:** The tool layer. Wrap your firm's data services. Get to ~30
working tools. This is the bulk of the engineering effort.

**Month 3:** First agent end-to-end. Probably research-analyst. Get one
role producing reasonable output on real tasks. Iterate AGENTS.md based on
what you see.

**Month 4:** Second and third agents. Risk-reviewer and macro-watcher. Wire
up `morning_routine.py`.

**Month 5:** Extensions. Audit logging, cost tracking, circuit breaker.
Production hardening.

**Month 5-6:** Eval suite. This is the hardest part because it requires
point-in-time data infrastructure. If you have it, weeks. If you don't,
months.

**Month 6:** Dashboard. Operations UI for humans.

**Month 6+:** Feedback loop and continuous improvement. Now you're in steady
state. Daily ops, weekly eval runs, quarterly philosophy reviews.

---

## The summary view

A finance harness built on pyharness is:

1. The **pyharness library** (your minimal, multi-vendor, headless agent runtime)
2. **30-50 finance-specific tools** (your wrappers around firm infrastructure)
3. **5-10 named agents** (markdown definitions for each role)
4. A handful of **skills** (for genuinely conditional capabilities)
5. **3-5 extensions** (audit, cost, circuit breaker, maybe more)
6. The **orchestration code** (Python parent processes that wire agents together)
7. **AGENTS.md hierarchy** (firm philosophy, role specifics, accumulated wisdom)
8. An **eval suite** (historical situations + scoring rubric)
9. A **feedback loop** (proposal -> trade -> outcome -> retrospective)
10. A **dashboard** (operations UI for humans)

Pyharness is maybe 5% of the total system by volume but 80% of the "agent
harness" by responsibility. Everything else is your firm's IP, expressed in
a form pyharness can run.

The interesting work — the work that gives the firm an edge — is the
AGENTS.md content, the tools, the eval design, and the feedback loop.
Pyharness just lets that work execute reliably and observably.
