"""Session lifecycle: resume, fork, corruption tolerance."""

from __future__ import annotations

import json

import pytest

from coding_harness import CodingAgent, CodingAgentConfig, Settings
from pyharness import LLMResponse, Session

from ._helpers import install_scripted_llm, make_project


@pytest.mark.asyncio
async def test_resume_reconstructs_transcript(tmp_path, isolated_session_dir):
    workspace = make_project(tmp_path)
    agent = CodingAgent(CodingAgentConfig(workspace=workspace, settings=Settings()))
    install_scripted_llm(agent, [LLMResponse(text="finished")])
    result = await agent.run("hello world")
    sid = result.session_id

    resumed = Session.resume(sid, base_dir=isolated_session_dir)
    assert resumed.session_id == sid
    msgs = resumed.read_messages()
    text = " ".join(str(m.content) for m in msgs)
    assert "hello world" in text
    assert "finished" in text


@pytest.mark.asyncio
async def test_fork_at_event_truncates(tmp_path, isolated_session_dir):
    workspace = make_project(tmp_path)
    agent = CodingAgent(CodingAgentConfig(workspace=workspace, settings=Settings()))
    install_scripted_llm(agent, [LLMResponse(text="ending")])
    result = await agent.run("hello")
    sid = result.session_id

    forked = Session.fork(sid, fork_at_event=1, base_dir=isolated_session_dir)
    assert forked.session_id != sid
    forked_events = forked.read_events()
    assert all(e.sequence_number <= 1 for e in forked_events)


def test_corrupt_jsonl_line_is_tolerated(tmp_path, isolated_session_dir):
    sid = "corruptedsessionidff00aabb1122334"
    log = isolated_session_dir / sid[:2] / f"{sid}.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(
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
    assert len(events) == 2
    assert events[0].type == "session_start"
    assert events[1].type == "user_message"


def test_resume_unknown_raises(tmp_path, isolated_session_dir):
    with pytest.raises(FileNotFoundError):
        Session.resume("nonexistent", base_dir=isolated_session_dir)
