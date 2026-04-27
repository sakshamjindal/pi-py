"""JSONL session-log shape validation.

After a run completes, the log on disk must satisfy:
  - First event is `session_start` with cwd, model, agent_name,
    system_prompt_hash, settings_snapshot.
  - Last event is `session_end` with a valid reason.
  - Every event has a unique event_id and a strictly monotonic
    sequence_number starting at 1.
  - Every tool_call_start has a matching tool_call_end with the same
    call_id; no orphans either way.
  - Assistant messages with tool_calls have well-formed entries
    (id, type='function', function.name, function.arguments).
  - The system_prompt_hash matches the agent's system_prompt at
    construction time.
  - Compaction events have non-negative tokens_before/after.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from coding_harness import CodingAgent, CodingAgentConfig, Settings
from pyharness import (
    AssistantMessageEvent,
    LLMResponse,
    SessionEndEvent,
    SessionStartEvent,
    ToolCall,
    ToolCallEndEvent,
    ToolCallStartEvent,
    UserMessageEvent,
)

from ._helpers import install_scripted_llm, make_project

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_shape_invariants(events) -> list[str]:
    """Return a list of failure messages; empty list means clean."""

    fails: list[str] = []

    if not events:
        return ["empty event list"]

    # First event = session_start.
    if not isinstance(events[0], SessionStartEvent):
        fails.append(f"first event is {type(events[0]).__name__}, expected SessionStartEvent")

    # Last event = session_end (assuming run completed; some failure
    # paths skip the session_end emit, which is itself worth flagging).
    if not isinstance(events[-1], SessionEndEvent):
        fails.append(f"last event is {type(events[-1]).__name__}, expected SessionEndEvent")

    # Unique event_ids.
    seen_ids: set[str] = set()
    for e in events:
        if e.event_id in seen_ids:
            fails.append(f"duplicate event_id {e.event_id!r}")
        seen_ids.add(e.event_id)

    # Strictly monotonic sequence_numbers starting at 1.
    expected = 1
    for e in events:
        if e.sequence_number != expected:
            fails.append(
                f"sequence_number break at event {e.event_id} "
                f"(got {e.sequence_number}, expected {expected})"
            )
            break
        expected += 1

    # Tool call start/end pairing.
    starts: dict[str, ToolCallStartEvent] = {}
    ends: dict[str, ToolCallEndEvent] = {}
    for e in events:
        if isinstance(e, ToolCallStartEvent):
            if e.call_id in starts:
                fails.append(f"duplicate tool_call_start for call_id={e.call_id}")
            starts[e.call_id] = e
        elif isinstance(e, ToolCallEndEvent):
            if e.call_id in ends:
                fails.append(f"duplicate tool_call_end for call_id={e.call_id}")
            ends[e.call_id] = e
    for cid in starts:
        if cid not in ends:
            fails.append(f"tool_call_start with no matching end: call_id={cid}")
    for cid in ends:
        if cid not in starts:
            fails.append(f"tool_call_end with no matching start: call_id={cid}")

    # Assistant messages with tool_calls have well-formed entries.
    for e in events:
        if not isinstance(e, AssistantMessageEvent):
            continue
        for tc in e.tool_calls or []:
            if not isinstance(tc, dict):
                fails.append(f"tool_call is not a dict in assistant message {e.event_id}")
                continue
            if tc.get("type") != "function":
                fails.append(f"tool_call type != 'function' in {e.event_id}: {tc}")
            if not tc.get("id"):
                fails.append(f"tool_call missing id in {e.event_id}")
            fn = tc.get("function") or {}
            if not fn.get("name"):
                fails.append(f"tool_call missing function.name in {e.event_id}")
            args = fn.get("arguments")
            if not isinstance(args, str):
                fails.append(f"tool_call arguments not a JSON string in {e.event_id}: {args!r}")
            else:
                try:
                    json.loads(args)
                except json.JSONDecodeError:
                    fails.append(f"tool_call arguments not parseable JSON in {e.event_id}")

    return fails


# ---------------------------------------------------------------------------
# Mock-mode tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_basic_run_log_shape(tmp_path, isolated_session_dir):
    """A no-tool run produces session_start → user_message →
    assistant_message → session_end. All invariants hold."""

    workspace = make_project(tmp_path)
    agent = CodingAgent(CodingAgentConfig(workspace=workspace, settings=Settings()))
    install_scripted_llm(agent, [LLMResponse(text="hello")])
    await agent.run("hi there")

    events = agent.session.read_events()
    fails = _check_shape_invariants(events)
    assert not fails, f"shape violations: {fails}"

    # Specific structural assertions.
    types = [type(e).__name__ for e in events]
    assert types == [
        "SessionStartEvent",
        "UserMessageEvent",
        "AssistantMessageEvent",
        "SessionEndEvent",
    ]

    # SessionStartEvent fields.
    s = events[0]
    assert isinstance(s, SessionStartEvent)
    assert s.cwd == str(workspace)
    assert s.model
    assert s.system_prompt_hash
    assert isinstance(s.settings_snapshot, dict)

    # system_prompt_hash matches agent.system_prompt (sha1 hex).
    expected_hash = hashlib.sha1(agent.system_prompt.encode("utf-8")).hexdigest()
    assert s.system_prompt_hash == expected_hash, (
        f"system_prompt_hash mismatch: log={s.system_prompt_hash} agent={expected_hash}"
    )

    # SessionEndEvent.
    e = events[-1]
    assert isinstance(e, SessionEndEvent)
    assert e.reason == "completed"


@pytest.mark.asyncio
async def test_tool_call_run_log_shape(tmp_path, isolated_session_dir):
    """A run that issues a tool call produces a paired
    tool_call_start/tool_call_end with matching call_ids."""

    workspace = make_project(tmp_path)
    (workspace / "x.txt").write_text("hi", encoding="utf-8")

    agent = CodingAgent(CodingAgentConfig(workspace=workspace, settings=Settings()))
    install_scripted_llm(
        agent,
        [
            LLMResponse(
                tool_calls=[ToolCall(id="call-A", name="read", arguments={"path": "x.txt"})]
            ),
            LLMResponse(text="contents acknowledged"),
        ],
    )
    await agent.run("read it")

    events = agent.session.read_events()
    fails = _check_shape_invariants(events)
    assert not fails, f"shape violations: {fails}"

    # Specific call_id pairing.
    starts = [e for e in events if isinstance(e, ToolCallStartEvent)]
    ends = [e for e in events if isinstance(e, ToolCallEndEvent)]
    assert len(starts) == 1 and len(ends) == 1
    assert starts[0].call_id == ends[0].call_id == "call-A"
    assert starts[0].tool_name == "read"
    assert starts[0].arguments == {"path": "x.txt"}
    assert ends[0].ok is True


@pytest.mark.asyncio
async def test_assistant_tool_calls_are_well_formed(tmp_path, isolated_session_dir):
    """The tool_calls list on an AssistantMessageEvent uses the
    OpenAI function-call shape that the LLM client accepts."""

    workspace = make_project(tmp_path)
    (workspace / "x.txt").write_text("hi", encoding="utf-8")
    agent = CodingAgent(CodingAgentConfig(workspace=workspace, settings=Settings()))
    install_scripted_llm(
        agent,
        [
            LLMResponse(
                tool_calls=[
                    ToolCall(id="c1", name="read", arguments={"path": "x.txt"}),
                ]
            ),
            LLMResponse(text="done"),
        ],
    )
    await agent.run("read it")
    events = agent.session.read_events()

    asst_with_tools = [e for e in events if isinstance(e, AssistantMessageEvent) and e.tool_calls]
    assert len(asst_with_tools) == 1
    tc = asst_with_tools[0].tool_calls[0]
    assert tc["id"] == "c1"
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "read"
    args_obj = json.loads(tc["function"]["arguments"])
    assert args_obj == {"path": "x.txt"}


@pytest.mark.asyncio
async def test_user_message_recorded(tmp_path, isolated_session_dir):
    """The initial prompt arrives in the log as a UserMessageEvent
    with the exact content the user passed."""

    workspace = make_project(tmp_path)
    agent = CodingAgent(CodingAgentConfig(workspace=workspace, settings=Settings()))
    install_scripted_llm(agent, [LLMResponse(text="ok")])
    await agent.run("THE EXACT PROMPT")
    events = agent.session.read_events()
    user_msgs = [e for e in events if isinstance(e, UserMessageEvent)]
    assert len(user_msgs) == 1
    assert user_msgs[0].content == "THE EXACT PROMPT"


@pytest.mark.asyncio
async def test_log_is_jsonl(tmp_path, isolated_session_dir):
    """The on-disk log is one JSON object per line, parseable line by line."""

    workspace = make_project(tmp_path)
    agent = CodingAgent(CodingAgentConfig(workspace=workspace, settings=Settings()))
    install_scripted_llm(agent, [LLMResponse(text="ok")])
    await agent.run("hi")

    log_path = agent.session.log_path
    assert log_path.is_file()
    raw_lines = log_path.read_text(encoding="utf-8").splitlines()
    assert raw_lines, "log is empty"
    for i, line in enumerate(raw_lines):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            pytest.fail(f"line {i} is not valid JSON: {e}; line: {line!r}")
        assert "type" in obj, f"line {i} missing 'type'"
        assert "event_id" in obj, f"line {i} missing 'event_id'"
        assert "sequence_number" in obj, f"line {i} missing 'sequence_number'"
