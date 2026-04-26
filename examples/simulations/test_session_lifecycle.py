"""Session lifecycle scenarios: resume, fork, corruption tolerance."""

from __future__ import annotations

import json

import pytest

from coding_harness import CodingAgent, CodingAgentConfig, Settings
from pyharness import LLMResponse, Session

from ._helpers import install_scripted_llm, make_project


@pytest.mark.asyncio
async def test_resume_reconstructs_transcript(tmp_path, isolated_session_dir):
    """After a run completes, ``Session.resume(id)`` returns a session
    object with the original transcript readable via ``read_messages``."""

    workspace = make_project(tmp_path)

    # First run.
    agent = CodingAgent(CodingAgentConfig(workspace=workspace, settings=Settings()))
    install_scripted_llm(agent, [LLMResponse(text="finished")])
    result = await agent.run("hello world")
    sid = result.session_id

    # Resume into a fresh Session object.
    resumed = Session.resume(sid, base_dir=isolated_session_dir)
    assert resumed.session_id == sid
    msgs = resumed.read_messages()
    # Must contain the user prompt + the assistant reply.
    text = " ".join(str(m.content) for m in msgs)
    assert "hello world" in text
    assert "finished" in text


@pytest.mark.asyncio
async def test_fork_at_event_truncates_history(tmp_path, isolated_session_dir):
    """``Session.fork(id, fork_at_event=N)`` produces a new session
    whose log contains only events with sequence_number <= N."""

    workspace = make_project(tmp_path)
    agent = CodingAgent(CodingAgentConfig(workspace=workspace, settings=Settings()))
    install_scripted_llm(agent, [LLMResponse(text="ending")])
    result = await agent.run("hello")
    sid = result.session_id

    # Fork at event 1 — should drop everything after.
    forked = Session.fork(sid, fork_at_event=1, base_dir=isolated_session_dir)
    forked_events = forked.read_events()
    # All forked events have sequence_number <= 1 (rewritten with new
    # session_id but original sequence numbers preserved).
    assert all(e.sequence_number <= 1 for e in forked_events)
    assert forked.session_id != sid


def test_session_with_corrupt_jsonl_line_is_tolerant(tmp_path, isolated_session_dir):
    """A session log with one malformed line must not crash
    ``read_events`` — the line is skipped."""

    sid = "corruptedsessionidff00aabb1122334"
    log = isolated_session_dir / sid[:2] / f"{sid}.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(
        # Valid line + corrupt line + valid line.
        json.dumps(
            {
                "type": "session_start",
                "session_id": sid,
                "sequence_number": 0,
                "timestamp": 1700000000.0,
                "cwd": str(tmp_path),
                "model": "fake",
                "agent_name": None,
                "system_prompt_hash": "abc123",
                "settings_snapshot": {},
            }
        )
        + "\n"
        + "this is not json {{{ }}}\n"
        + json.dumps(
            {
                "type": "user_message",
                "session_id": sid,
                "sequence_number": 1,
                "timestamp": 1700000001.0,
                "content": "hello",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    s = Session.resume(sid, base_dir=isolated_session_dir)
    events = s.read_events()
    # The two valid events must be readable; the corrupt line is skipped.
    assert len(events) == 2
    assert events[0].type == "session_start"
    assert events[1].type == "user_message"


def test_resume_unknown_session_raises(tmp_path, isolated_session_dir):
    with pytest.raises(FileNotFoundError):
        Session.resume("nonexistent", base_dir=isolated_session_dir)
