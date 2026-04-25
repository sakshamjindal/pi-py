"""Core agent loop.

This is the heart of the harness. The loop is straight-line code:

  for turn in range(max_turns):
      drain steering / follow-up queues
      maybe compact
      call LLM
      if no tool_calls: done
      execute each tool_call (checking steering between calls)

Lifecycle events are emitted to the event bus so extensions can observe
or override; the session log records the durable record of what happened.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .compaction import Compactor
from .config import Settings
from .events import (
    AssistantMessageEvent,
    CompactionEvent,
    FollowUpMessageEvent,
    LifecycleEvent,
    SessionEndEvent,
    SessionStartEvent,
    SkillLoadedEvent,
    SteeringMessageEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    UserMessageEvent,
)
from .extensions import EventBus, ExtensionAPI, HandlerContext, HookOutcome, HookResult, load_extensions
from .llm import LLMClient, count_tokens
from .queues import HarnessHandle, MessageQueue
from .skills import LoadSkillTool, SkillDefinition, build_skill_index, discover_skills
from .session import Session
from .tools.base import Tool, ToolContext, ToolRegistry, execute_tool
from .tools.builtin import builtin_registry
from .types import Message, RunResult
from .workspace import WorkspaceContext


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class HarnessConfig:
    workspace: Path
    model: str | None = None
    agent_name: str | None = None
    max_turns: int | None = None
    settings: Settings | None = None
    bare: bool = False
    project_root: Path | None = None
    session: Session | None = None
    fork_from: str | None = None
    fork_at_event: int | None = None
    resume_from: str | None = None
    extra_messages: list[Message] = field(default_factory=list)
    cli_overrides: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


BASE_SYSTEM_PROMPT = (
    "You are pyharness, an LLM-driven agent running in a headless harness.\n"
    "You receive a task and complete it by calling the tools available to "
    "you. When the task is done, reply with a final answer and no tool "
    "calls.\n\n"
    "Operating principles:\n"
    "- Files are the durable layer. Use the file tools to inspect and edit.\n"
    "- Be concise in user-facing replies. Provide concrete output, not narration.\n"
    "- Prefer one tool call at a time when reasoning is involved.\n"
    "- If a tool returns an error, read the message and adjust before retrying.\n"
)


class Harness:
    def __init__(self, config: HarnessConfig):
        self.config = config
        self.workspace_ctx = WorkspaceContext(
            workspace=config.workspace, project_root=config.project_root
        )
        self.settings = config.settings or Settings.load(
            workspace=self.workspace_ctx.workspace,
            project_root=self.workspace_ctx.project_root,
            cli_overrides=config.cli_overrides,
        )
        self.model = config.model or self.settings.default_model
        self.max_turns = config.max_turns or self.settings.max_turns
        self.run_id = uuid.uuid4().hex
        self.event_bus = EventBus()
        self.session = self._make_session()
        self.tool_registry = ToolRegistry()
        self.skills: dict[str, SkillDefinition] = {}
        self.agent_def = None
        self.system_prompt: str = ""
        self.extensions_loaded: list[str] = []
        self.llm = LLMClient()
        self.compactor = Compactor(
            self.llm,
            summarization_model=self.settings.summarization_model,
            keep_recent_count=self.settings.keep_recent_count,
        )
        self._steering = MessageQueue()
        self._followup = MessageQueue()
        self._abort_event = asyncio.Event()
        self._cost_total = 0.0
        self._files_written: list[str] = []
        self._setup()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _make_session(self) -> Session:
        if self.config.session is not None:
            return self.config.session
        if self.config.resume_from:
            return Session.resume(self.config.resume_from)
        if self.config.fork_from:
            return Session.fork(
                self.config.fork_from, fork_at_event=self.config.fork_at_event
            )
        return Session.new(self.workspace_ctx.workspace)

    def _setup(self) -> None:
        self._build_tool_registry()
        self.skills = discover_skills(self.workspace_ctx)
        # Register the load_skill tool last so the registry already
        # contains the agent's other tools.
        self.tool_registry.register(LoadSkillTool(self.skills, self.tool_registry, on_load=self._on_skill_loaded))
        self.system_prompt = self._build_system_prompt()

        if not self.config.bare:
            api = ExtensionAPI(
                bus=self.event_bus,
                registry=self.tool_registry,
                settings=self.settings,
                session_appender=None,
            )
            loaded = load_extensions(api, self.workspace_ctx.collect_extensions_dirs())
            self.extensions_loaded = loaded.modules

    def _build_tool_registry(self) -> None:
        if self.config.agent_name:
            from .agents import discover_agents, load_agent_definition, resolve_tool_list

            agents = discover_agents(self.workspace_ctx)
            if self.config.agent_name not in agents:
                raise ValueError(
                    f"Unknown agent: {self.config.agent_name!r}. Known: "
                    f"{sorted(agents.keys())}"
                )
            self.agent_def = load_agent_definition(agents[self.config.agent_name])
            if self.agent_def.model and not self.config.model:
                self.model = self.agent_def.model
            self.tool_registry = resolve_tool_list(
                self.agent_def.tools,
                self.workspace_ctx,
                agent_name=self.agent_def.name,
            )
        else:
            self.tool_registry = builtin_registry()

    def _build_system_prompt(self) -> str:
        parts = [BASE_SYSTEM_PROMPT.strip()]
        if not self.config.bare:
            agents_md = self.workspace_ctx.render_agents_md()
            if agents_md:
                parts.append(agents_md.strip())
        if self.agent_def is not None and self.agent_def.body.strip():
            parts.append(self.agent_def.body.strip())
        skill_index = build_skill_index(self.skills)
        if skill_index:
            parts.append(skill_index.strip())
        return "\n\n".join(parts)

    async def _on_skill_loaded(self, skill: SkillDefinition, added: list[str]) -> None:
        await self.session.append_event(
            SkillLoadedEvent(session_id=self.session.session_id, name=skill.name, tools_added=added)
        )

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    async def run(self, prompt: str) -> RunResult:
        return await self._loop(prompt)

    def start(self, prompt: str) -> HarnessHandle:
        task = asyncio.create_task(self._loop(prompt))
        return HarnessHandle(
            steering=self._steering,
            follow_up=self._followup,
            abort_event=self._abort_event,
            task=task,
        )

    # ------------------------------------------------------------------
    # The loop
    # ------------------------------------------------------------------

    async def _loop(self, initial_prompt: str) -> RunResult:
        await self._emit_session_start(initial_prompt)

        # Reconstruct messages on resume; otherwise start fresh.
        messages: list[Message] = []
        messages.append(Message(role="system", content=self.system_prompt))
        prior = self.session.read_messages() if self.config.resume_from or self.config.fork_from else []
        for m in prior:
            messages.append(m)
        for m in self.config.extra_messages:
            messages.append(m)

        if initial_prompt:
            await self.session.append_event(
                UserMessageEvent(session_id=self.session.session_id, content=initial_prompt)
            )
            messages.append(Message(role="user", content=initial_prompt))

        turn = 0
        final_text = ""
        completed = False
        reason = "max_turns"

        while turn < self.max_turns:
            if self._abort_event.is_set():
                reason = "aborted"
                break
            turn += 1

            messages = await self._drain_queues_into(messages, kind="both")

            messages = await self._maybe_compact(messages)

            await self._emit_lifecycle("turn_start", {"turn": turn})

            outcome = await self._emit_lifecycle(
                "before_llm_call", {"messages": [m.model_dump() for m in messages]}
            )
            if outcome.result is HookResult.Deny:
                reason = "error"
                final_text = f"LLM call denied by extension: {outcome.reason}"
                break

            try:
                response = await self.llm.complete(
                    model=self.model,
                    messages=messages,
                    tools=self.tool_registry.list_specs() or None,
                    max_tokens=self.settings.model_dump().get("max_tokens"),
                )
            except Exception as exc:
                reason = "error"
                final_text = f"LLM error: {exc}"
                break

            self._cost_total += response.usage.cost_usd

            await self._emit_lifecycle("after_llm_call", {"response": response.model_dump()})

            tc_dicts = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                }
                for tc in response.tool_calls
            ]

            await self.session.append_event(
                AssistantMessageEvent(
                    session_id=self.session.session_id,
                    text=response.text,
                    thinking=response.thinking,
                    tool_calls=tc_dicts,
                )
            )
            messages.append(
                Message(
                    role="assistant",
                    content=response.text,
                    tool_calls=tc_dicts or None,
                )
            )

            if not response.tool_calls:
                final_text = response.text
                completed = True
                reason = "completed"
                break

            steered_mid_turn = False
            for tc in response.tool_calls:
                if self._abort_event.is_set():
                    reason = "aborted"
                    break

                tool: Tool | None = self.tool_registry.get(tc.name)
                if tool is None:
                    err = f"Unknown tool: {tc.name}"
                    await self._record_tool_end(tc.id, tc.name, ok=False, content=err, error="unknown_tool")
                    messages.append(
                        Message(role="tool", tool_call_id=tc.id, name=tc.name, content=err)
                    )
                    continue

                hook_outcome = await self._emit_lifecycle(
                    "before_tool_call",
                    {"tool_name": tc.name, "arguments": tc.arguments, "call_id": tc.id},
                )
                if hook_outcome.result is HookResult.Deny:
                    msg = f"Denied by extension: {hook_outcome.reason or 'no reason given'}"
                    await self._record_tool_end(tc.id, tc.name, ok=False, content=msg, error="denied")
                    messages.append(
                        Message(role="tool", tool_call_id=tc.id, name=tc.name, content=msg)
                    )
                    continue
                if hook_outcome.result is HookResult.Replace:
                    replacement = hook_outcome.replacement_value
                    text = replacement if isinstance(replacement, str) else json.dumps(replacement, default=str)
                    await self._record_tool_end(tc.id, tc.name, ok=True, content=text)
                    messages.append(
                        Message(role="tool", tool_call_id=tc.id, name=tc.name, content=text)
                    )
                    continue

                await self.session.append_event(
                    ToolCallStartEvent(
                        session_id=self.session.session_id,
                        call_id=tc.id,
                        tool_name=tc.name,
                        arguments=tc.arguments,
                    )
                )

                ctx = ToolContext(
                    workspace=self.workspace_ctx.workspace,
                    session_id=self.session.session_id,
                    run_id=self.run_id,
                    event_bus=self.event_bus,
                    settings=self.settings,
                    extras={"files_written": self._files_written},
                )

                result = await execute_tool(
                    tool,
                    tc.arguments,
                    ctx,
                    timeout_seconds=self._timeout_for_tool(tool.name),
                    max_bytes=self.settings.tool_output_max_bytes,
                    max_lines=self.settings.tool_output_max_lines,
                )

                await self.session.append_event(
                    ToolCallEndEvent(
                        session_id=self.session.session_id,
                        call_id=tc.id,
                        tool_name=tc.name,
                        ok=result.ok,
                        result=result.content,
                        error=result.error,
                        duration_ms=result.duration_ms,
                    )
                )
                messages.append(
                    Message(
                        role="tool",
                        tool_call_id=tc.id,
                        name=tc.name,
                        content=result.content,
                    )
                )

                await self._emit_lifecycle(
                    "after_tool_call",
                    {
                        "tool_name": tc.name,
                        "ok": result.ok,
                        "result": result.content,
                        "duration_ms": result.duration_ms,
                    },
                )

                # Steering check between tool calls.
                if not self._steering.empty():
                    steered_mid_turn = True
                    break

            if steered_mid_turn:
                messages = await self._drain_queues_into(messages, kind="steering")
                continue

            await self._emit_lifecycle("turn_end", {"turn": turn})

        if not completed and reason == "max_turns":
            final_text = "Reached max_turns without completing."

        await self.session.append_event(
            SessionEndEvent(
                session_id=self.session.session_id,
                reason=reason,  # type: ignore[arg-type]
                final_message=final_text,
            )
        )
        await self._emit_lifecycle("session_end", {"reason": reason, "final_message": final_text})

        return RunResult(
            session_id=self.session.session_id,
            final_output=final_text,
            turn_count=turn,
            cost=self._cost_total,
            files_written=list(self._files_written),
            completed=completed,
            reason=reason,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _drain_queues_into(self, messages: list[Message], *, kind: str) -> list[Message]:
        if kind in ("steering", "both"):
            for content in await self._steering.drain():
                await self.session.append_event(
                    SteeringMessageEvent(session_id=self.session.session_id, content=content)
                )
                await self._emit_lifecycle("steering_received", {"content": content})
                messages.append(Message(role="user", content=f"[steering] {content}"))
        if kind in ("followup", "both"):
            for content in await self._followup.drain():
                await self.session.append_event(
                    FollowUpMessageEvent(session_id=self.session.session_id, content=content)
                )
                await self._emit_lifecycle("followup_received", {"content": content})
                messages.append(Message(role="user", content=content))
        return messages

    async def _maybe_compact(self, messages: list[Message]) -> list[Message]:
        threshold = int(self.settings.model_context_window * self.settings.compaction_threshold_pct)
        await self._emit_lifecycle("compaction_start", {})
        result = await self.compactor.maybe_compact(
            messages, threshold, model_for_count=self.model
        )
        if result.compacted:
            await self.session.append_event(
                CompactionEvent(
                    session_id=self.session.session_id,
                    tokens_before=result.tokens_before,
                    tokens_after=result.tokens_after,
                    summary=result.summary,
                )
            )
        await self._emit_lifecycle(
            "compaction_end",
            {
                "compacted": result.compacted,
                "tokens_before": result.tokens_before,
                "tokens_after": result.tokens_after,
            },
        )
        return result.messages

    async def _record_tool_end(self, call_id: str, name: str, *, ok: bool, content: str, error: str | None = None) -> None:
        await self.session.append_event(
            ToolCallEndEvent(
                session_id=self.session.session_id,
                call_id=call_id,
                tool_name=name,
                ok=ok,
                result=content,
                error=error,
            )
        )

    def _timeout_for_tool(self, name: str) -> float | None:
        if name == "bash":
            return float(self.settings.bash_timeout_seconds + 5)
        if name in ("web_fetch", "web_search"):
            return float(self.settings.fetch_timeout_seconds + 5)
        return None

    async def _emit_session_start(self, initial_prompt: str) -> None:
        digest = hashlib.sha1(self.system_prompt.encode("utf-8")).hexdigest()
        await self.session.append_event(
            SessionStartEvent(
                session_id=self.session.session_id,
                cwd=str(self.workspace_ctx.workspace),
                model=self.model,
                agent_name=self.agent_def.name if self.agent_def else None,
                system_prompt_hash=digest,
                settings_snapshot=self.settings.model_dump(),
            )
        )
        await self._emit_lifecycle("session_start", {"prompt": initial_prompt, "model": self.model})

    async def _emit_lifecycle(self, name: str, payload: dict[str, Any]) -> HookOutcome:
        ctx = HandlerContext(
            settings=self.settings,
            workspace=self.workspace_ctx.workspace,
            session_id=self.session.session_id,
            run_id=self.run_id,
        )
        return await self.event_bus.emit(LifecycleEvent(name=name, payload=payload), ctx)
