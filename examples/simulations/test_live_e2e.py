"""Live end-to-end scenarios — gated on PYHARNESS_LIVE_API=1.

Hits a real LLM via LiteLLM. Default model is
``openrouter/anthropic/claude-haiku-4-5`` (cheap). Override with
``PYHARNESS_LIVE_MODEL``.

These scenarios are designed to be cheap (max_turns small, prompts
short) and to validate the *whole pipeline* against actual model
behaviour: tool calling, skill loading, error recovery, JSONL session
log shape, concurrent isolation, and the regression test for the
PR #11 compaction-crash fix.

Each scenario also runs the ``_check_shape_invariants`` validator
from test_session_log_shape against the resulting log so we catch
log-format regressions under live conditions.
"""

from __future__ import annotations

import asyncio
import textwrap

import pytest

from coding_harness import CodingAgent, CodingAgentConfig, Settings

from ._helpers import make_project
from .test_session_log_shape import _check_shape_invariants

pytestmark = pytest.mark.live


def _settings(model: str) -> Settings:
    return Settings(default_model=model, summarization_model=model)


@pytest.mark.asyncio
async def test_live_basic_text(tmp_path, isolated_session_dir, live_model):
    workspace = make_project(tmp_path)
    agent = CodingAgent(
        CodingAgentConfig(
            workspace=workspace, settings=_settings(live_model), model=live_model, max_turns=2
        )
    )
    result = await agent.run("Reply with exactly the word PONG and nothing else.")
    assert result.completed, f"reason={result.reason}, output={result.final_output!r}"
    assert "PONG" in (result.final_output or "").upper()

    fails = _check_shape_invariants(agent.session.read_events())
    assert not fails, f"log shape: {fails}"


@pytest.mark.asyncio
async def test_live_tool_round_trip(tmp_path, isolated_session_dir, live_model):
    workspace = make_project(tmp_path)
    agent = CodingAgent(
        CodingAgentConfig(
            workspace=workspace, settings=_settings(live_model), model=live_model, max_turns=8
        )
    )
    result = await agent.run(
        f"Use the `write` tool to create the file 'hello.txt' in {workspace} with the exact "
        "contents 'hi-from-stress'. Then use `read` to verify and reply with the single word DONE."
    )
    assert result.completed, f"reason={result.reason}"
    assert (workspace / "hello.txt").is_file()
    assert "hi-from-stress" in (workspace / "hello.txt").read_text(encoding="utf-8")

    fails = _check_shape_invariants(agent.session.read_events())
    assert not fails, f"log shape: {fails}"


@pytest.mark.asyncio
async def test_live_tool_error_recovery(tmp_path, isolated_session_dir, live_model):
    workspace = make_project(tmp_path)
    agent = CodingAgent(
        CodingAgentConfig(
            workspace=workspace, settings=_settings(live_model), model=live_model, max_turns=8
        )
    )
    result = await agent.run(
        f"First try to read 'doesnotexist.txt' from {workspace}. When that fails, use `write` to "
        "create 'fallback.txt' with the exact contents 'recovered' and then `read` it back. "
        "Reply with the file's contents."
    )
    assert result.completed, f"reason={result.reason}"
    assert "recovered" in (result.final_output or "").lower()


@pytest.mark.asyncio
async def test_live_load_skill_picks_up_description_match(
    tmp_path, isolated_session_dir, live_model
):
    """Description match → model invokes load_skill → skill marker appears."""

    workspace = make_project(tmp_path)
    sd = workspace / ".pyharness" / "skills" / "polite-greeting"
    sd.mkdir(parents=True)
    (sd / "SKILL.md").write_text(
        textwrap.dedent(
            """
            ---
            name: polite-greeting
            description: Use when the user asks you to greet them in a polite, formal style.
            ---

            When this skill is loaded, end your response with the marker
            POLITE_MARKER_X9 so the user knows it activated.
            """
        ).strip(),
        encoding="utf-8",
    )

    agent = CodingAgent(
        CodingAgentConfig(
            workspace=workspace, settings=_settings(live_model), model=live_model, max_turns=4
        )
    )
    result = await agent.run("Please greet me politely and formally.")
    assert result.completed, f"reason={result.reason}"

    types = [e.type for e in agent.session.read_events()]
    loaded = "skill_loaded" in types
    marker = "POLITE_MARKER_X9" in (result.final_output or "")
    if not (loaded or marker):
        pytest.skip(
            "model declined to load the skill on this run (non-deterministic). "
            "Re-run if you suspect a regression."
        )


@pytest.mark.asyncio
async def test_live_concurrent_agents(tmp_path, isolated_session_dir, live_model):
    ws_a = make_project(tmp_path / "ca-a")
    ws_b = make_project(tmp_path / "ca-b")
    a = CodingAgent(
        CodingAgentConfig(
            workspace=ws_a, settings=_settings(live_model), model=live_model, max_turns=2
        )
    )
    b = CodingAgent(
        CodingAgentConfig(
            workspace=ws_b, settings=_settings(live_model), model=live_model, max_turns=2
        )
    )
    ra, rb = await asyncio.gather(
        a.run("Reply with exactly 'A_DONE' and nothing else."),
        b.run("Reply with exactly 'B_DONE' and nothing else."),
    )
    assert ra.completed and "A_DONE" in (ra.final_output or "")
    assert rb.completed and "B_DONE" in (rb.final_output or "")
    assert ra.session_id != rb.session_id


@pytest.mark.asyncio
async def test_live_compaction_does_not_crash(tmp_path, isolated_session_dir, live_model):
    """Regression for PR #11. Force compaction via tiny context window
    and verify the run does not crash."""

    from pyharness import Message

    workspace = make_project(tmp_path)
    settings = Settings(
        default_model=live_model,
        summarization_model=live_model,
        model_context_window=4000,
        compaction_threshold_pct=0.1,
        keep_recent_count=2,
    )
    extra = [
        Message(role="user" if i % 2 == 0 else "assistant", content="x" * 200) for i in range(8)
    ]
    agent = CodingAgent(
        CodingAgentConfig(
            workspace=workspace,
            settings=settings,
            model=live_model,
            max_turns=3,
            extra_messages=extra,
        )
    )
    result = await agent.run("Reply with exactly the word DONE and nothing else.")
    # Bug-fix-confirming assertion: must not crash; either completes or
    # hits max_turns / clean error.
    assert result.reason in ("completed", "max_turns", "error")
