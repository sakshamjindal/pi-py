"""JSONL session log: append-only, atomic writes, fork+resume support."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .events import (
    AgentEvent,
    AssistantMessageEvent,
    CompactionEvent,
    FollowUpMessageEvent,
    SessionStartEvent,
    SteeringMessageEvent,
    ToolCallEndEvent,
    UserMessageEvent,
    parse_event,
)
from .types import Message


def _cwd_hash(cwd: Path) -> str:
    return hashlib.sha1(str(cwd.resolve()).encode("utf-8")).hexdigest()[:16]


def _default_session_dir() -> Path:
    return Path(
        os.environ.get("PYHARNESS_SESSION_DIR", str(Path.home() / ".pyharness" / "sessions"))
    )


@dataclass
class SessionInfo:
    session_id: str
    log_path: Path
    cwd: Path
    started_at: float
    model: str | None
    agent_name: str | None


class Session:
    """A single session is one JSONL file containing every event."""

    def __init__(
        self,
        *,
        session_id: str,
        cwd: Path,
        log_path: Path,
    ):
        self.session_id = session_id
        self.cwd = cwd
        self.log_path = log_path
        self._lock = asyncio.Lock()
        self._sequence: int = 0
        if log_path.exists():
            try:
                with log_path.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                            self._sequence = max(self._sequence, int(obj.get("sequence_number", 0)))
                        except json.JSONDecodeError:
                            continue
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def new(cls, cwd: Path, *, base_dir: Path | None = None) -> Session:
        cwd = Path(cwd).resolve()
        base = (base_dir or _default_session_dir()).expanduser()
        dir_ = base / _cwd_hash(cwd)
        dir_.mkdir(parents=True, exist_ok=True)
        sid = uuid.uuid4().hex
        log = dir_ / f"{sid}.jsonl"
        return cls(session_id=sid, cwd=cwd, log_path=log)

    @classmethod
    def resume(cls, session_id: str, *, base_dir: Path | None = None) -> Session:
        path = cls.find_log(session_id, base_dir=base_dir)
        if path is None:
            raise FileNotFoundError(f"No session log found for {session_id}")
        cwd = cls._cwd_from_log(path)
        return cls(session_id=session_id, cwd=cwd, log_path=path)

    @classmethod
    def fork(
        cls,
        source_session_id: str,
        *,
        fork_at_event: int | None = None,
        base_dir: Path | None = None,
    ) -> Session:
        src_path = cls.find_log(source_session_id, base_dir=base_dir)
        if src_path is None:
            raise FileNotFoundError(f"No session log found for {source_session_id}")
        cwd = cls._cwd_from_log(src_path)
        new = cls.new(cwd, base_dir=base_dir)

        with (
            src_path.open("r", encoding="utf-8") as src_fh,
            new.log_path.open("w", encoding="utf-8") as dst_fh,
        ):
            for line in src_fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if fork_at_event is not None and int(obj.get("sequence_number", 0)) > fork_at_event:
                    break
                obj["session_id"] = new.session_id
                dst_fh.write(json.dumps(obj) + "\n")
        new._sequence = new._highest_seq()
        return new

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------

    async def append_event(self, event: AgentEvent) -> AgentEvent:
        async with self._lock:
            self._sequence += 1
            event = event.model_copy(
                update={"session_id": self.session_id, "sequence_number": self._sequence}
            )
            data = event.model_dump_json()
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(data + "\n")
                fh.flush()
                with contextlib.suppress(OSError):
                    os.fsync(fh.fileno())
            return event

    def read_events(self) -> list[AgentEvent]:
        if not self.log_path.exists():
            return []
        events: list[AgentEvent] = []
        with self.log_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                events.append(parse_event(obj))
        return events

    def read_messages(self) -> list[Message]:
        """Reconstruct the LLM message history from logged events.

        We synthesize one assistant message per ``AssistantMessageEvent``
        and one tool message per ``ToolCallEndEvent``. ``CompactionEvent``s
        replace earlier user/assistant/tool messages with a synthetic
        summary, which mirrors what the Compactor does in-memory.
        """

        events = self.read_events()
        messages: list[Message] = []
        compaction_summary: str | None = None
        for ev in events:
            if isinstance(ev, SessionStartEvent):
                continue
            if isinstance(ev, UserMessageEvent):
                messages.append(Message(role="user", content=ev.content))
            elif isinstance(ev, SteeringMessageEvent):
                messages.append(Message(role="user", content=f"[steering] {ev.content}"))
            elif isinstance(ev, FollowUpMessageEvent):
                messages.append(Message(role="user", content=ev.content))
            elif isinstance(ev, AssistantMessageEvent):
                messages.append(
                    Message(
                        role="assistant",
                        content=ev.text,
                        tool_calls=ev.tool_calls or None,
                    )
                )
            elif isinstance(ev, ToolCallEndEvent):
                messages.append(
                    Message(
                        role="tool",
                        content=ev.result,
                        tool_call_id=ev.call_id,
                        name=ev.tool_name,
                    )
                )
            elif isinstance(ev, CompactionEvent):
                compaction_summary = ev.summary
                # Replace history with the summary marker; later messages
                # accumulate after.
                messages = [Message(role="user", content=f"[compacted summary]\n{ev.summary}")]
        if compaction_summary is None:
            return messages
        return messages

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    @classmethod
    def find_log(cls, session_id: str, *, base_dir: Path | None = None) -> Path | None:
        base = (base_dir or _default_session_dir()).expanduser()
        if not base.exists():
            return None
        for cwd_dir in base.iterdir():
            if not cwd_dir.is_dir():
                continue
            candidate = cwd_dir / f"{session_id}.jsonl"
            if candidate.is_file():
                return candidate
        return None

    @classmethod
    def list_recent(
        cls,
        cwd: Path | None = None,
        n: int = 10,
        *,
        base_dir: Path | None = None,
    ) -> list[SessionInfo]:
        base = (base_dir or _default_session_dir()).expanduser()
        if not base.exists():
            return []

        if cwd is not None:
            dirs = [base / _cwd_hash(Path(cwd).resolve())]
        else:
            dirs = [d for d in base.iterdir() if d.is_dir()]

        infos: list[SessionInfo] = []
        for d in dirs:
            if not d.exists():
                continue
            for log in d.glob("*.jsonl"):
                info = cls._first_event_info(log)
                if info is not None:
                    infos.append(info)
        infos.sort(key=lambda s: s.started_at, reverse=True)
        return infos[:n]

    @classmethod
    def _cwd_from_log(cls, log_path: Path) -> Path:
        try:
            with log_path.open("r", encoding="utf-8") as fh:
                first = fh.readline().strip()
                if first:
                    obj = json.loads(first)
                    if obj.get("type") == "session_start":
                        return Path(obj.get("cwd", "."))
        except (OSError, json.JSONDecodeError):
            pass
        return Path.cwd()

    @classmethod
    def _first_event_info(cls, log_path: Path) -> SessionInfo | None:
        try:
            with log_path.open("r", encoding="utf-8") as fh:
                first = fh.readline().strip()
        except OSError:
            return None
        if not first:
            return None
        try:
            obj = json.loads(first)
        except json.JSONDecodeError:
            return None
        if obj.get("type") != "session_start":
            return None
        return SessionInfo(
            session_id=obj.get("session_id", log_path.stem),
            log_path=log_path,
            cwd=Path(obj.get("cwd", ".")),
            started_at=float(obj.get("timestamp", time.time())),
            model=obj.get("model"),
            agent_name=obj.get("agent_name"),
        )

    def _highest_seq(self) -> int:
        seq = 0
        with self.log_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                seq = max(seq, int(obj.get("sequence_number", 0)))
        return seq
