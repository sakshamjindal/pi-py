"""Session JSONL: append, read back, list, fork."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pyharness.events import (
    AssistantMessageEvent,
    SessionEndEvent,
    SessionStartEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    UserMessageEvent,
)
from pyharness.session import Session


@pytest.mark.asyncio
async def test_new_session_creates_log(tmp_path):
    s = Session.new(tmp_path, base_dir=tmp_path / "sessions")
    await s.append_event(
        SessionStartEvent(
            session_id=s.session_id,
            cwd=str(tmp_path),
            model="x",
            system_prompt_hash="h",
        )
    )
    assert s.log_path.is_file()
    text = s.log_path.read_text(encoding="utf-8")
    assert '"type":"session_start"' in text


@pytest.mark.asyncio
async def test_read_messages_round_trip(tmp_path):
    s = Session.new(tmp_path, base_dir=tmp_path / "sessions")
    await s.append_event(
        SessionStartEvent(session_id=s.session_id, cwd=".", model="x", system_prompt_hash="h")
    )
    await s.append_event(UserMessageEvent(session_id=s.session_id, content="hi"))
    await s.append_event(
        AssistantMessageEvent(
            session_id=s.session_id,
            text="ok",
            tool_calls=[
                {"id": "c1", "type": "function", "function": {"name": "echo", "arguments": "{}"}}
            ],
        )
    )
    await s.append_event(
        ToolCallEndEvent(
            session_id=s.session_id, call_id="c1", tool_name="echo", ok=True, result="hi"
        )
    )
    await s.append_event(
        SessionEndEvent(session_id=s.session_id, reason="completed", final_message="done")
    )

    messages = s.read_messages()
    roles = [m.role for m in messages]
    assert "user" in roles and "assistant" in roles and "tool" in roles


@pytest.mark.asyncio
async def test_fork_at_event(tmp_path):
    base = tmp_path / "sessions"
    s = Session.new(tmp_path, base_dir=base)
    await s.append_event(
        SessionStartEvent(
            session_id=s.session_id, cwd=str(tmp_path), model="x", system_prompt_hash="h"
        )
    )
    await s.append_event(UserMessageEvent(session_id=s.session_id, content="one"))
    await s.append_event(UserMessageEvent(session_id=s.session_id, content="two"))
    await s.append_event(UserMessageEvent(session_id=s.session_id, content="three"))

    forked = Session.fork(s.session_id, fork_at_event=2, base_dir=base)
    events = forked.read_events()
    # Should contain only the first two events from the source.
    contents = [getattr(ev, "content", None) for ev in events if hasattr(ev, "content")]
    assert "one" in contents
    assert "two" not in contents  # we cut off after seq 2 (start + first user)


@pytest.mark.asyncio
async def test_list_recent(tmp_path):
    base = tmp_path / "sessions"
    s = Session.new(tmp_path, base_dir=base)
    await s.append_event(
        SessionStartEvent(
            session_id=s.session_id, cwd=str(tmp_path), model="x", system_prompt_hash="h"
        )
    )
    items = Session.list_recent(tmp_path, base_dir=base)
    assert len(items) == 1
    assert items[0].session_id == s.session_id
