"""The coding-agent assembly layer.

``CodingAgent`` is the closest analog to the old ``Harness`` class. It
takes a workspace + (optional) named agent + settings, walks AGENTS.md,
discovers skills, loads extensions, builds a tool registry, renders the
system prompt, and constructs a ``pyharness.Agent`` to run the loop.

The SDK kernel (``pyharness.Agent``) is dumb on purpose: it only knows
about messages, tools, sessions, events. Everything that makes
pyharness a coding agent — file conventions, named sub-agents, skills,
extension discovery, settings hierarchy — lives here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pyharness import (
    Agent,
    AgentHandle,
    AgentOptions,
    Compactor,
    EventBus,
    ExtensionAPI,
    LLMClient,
    Message,
    RunResult,
    Session,
    SkillLoadedEvent,
    ToolRegistry,
)

from .agents import discover_agents, load_agent_definition, resolve_tool_list
from .config import Settings
from .extensions_loader import load_extensions
from .skills import LoadSkillTool, SkillDefinition, build_skill_index, discover_skills
from .tools.builtin import builtin_registry
from .workspace import WorkspaceContext

BASE_SYSTEM_PROMPT = (
    "You are an LLM-driven agent running in a headless harness. You receive "
    "a task and complete it by calling the tools available to you. When the "
    "task is done, reply with a final answer and no tool calls.\n\n"
    "Operating principles:\n"
    "- Be concise in user-facing replies. Provide concrete output, not narration.\n"
    "- Prefer one tool call at a time when reasoning is involved.\n"
    "- If a tool returns an error, read the message and adjust before retrying.\n"
)


@dataclass
class CodingAgentConfig:
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


class CodingAgent:
    """Assembles a pyharness ``Agent`` with coding-agent defaults."""

    def __init__(self, config: CodingAgentConfig):
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
        self.tool_registry: ToolRegistry = ToolRegistry()
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
        self._setup()
        self._agent = self._build_agent()

    @property
    def _steering(self):
        # Backwards-compatible accessor used by tests that want to push
        # into the steering queue before calling run().
        return self._agent._steering

    @property
    def _followup(self):
        return self._agent._followup

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _make_session(self) -> Session:
        if self.config.session is not None:
            return self.config.session
        if self.config.resume_from:
            return Session.resume(self.config.resume_from)
        if self.config.fork_from:
            return Session.fork(self.config.fork_from, fork_at_event=self.config.fork_at_event)
        return Session.new(self.workspace_ctx.workspace)

    def _setup(self) -> None:
        self._build_tool_registry()
        self.skills = discover_skills(self.workspace_ctx)
        # Register the load_skill tool last so the registry already
        # contains the agent's other tools.
        self.tool_registry.register(
            LoadSkillTool(self.skills, self.tool_registry, on_load=self._on_skill_loaded)
        )
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
            agents = discover_agents(self.workspace_ctx)
            if self.config.agent_name not in agents:
                raise ValueError(
                    f"Unknown agent: {self.config.agent_name!r}. Known: {sorted(agents.keys())}"
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

    def _build_agent(self) -> Agent:
        options = AgentOptions(
            model=self.model,
            max_turns=self.max_turns,
            model_context_window=self.settings.model_context_window,
            compaction_threshold_pct=self.settings.compaction_threshold_pct,
            tool_output_max_bytes=self.settings.tool_output_max_bytes,
            tool_output_max_lines=self.settings.tool_output_max_lines,
            tool_timeouts={
                "bash": float(self.settings.bash_timeout_seconds + 5),
                "web_fetch": float(self.settings.fetch_timeout_seconds + 5),
                "web_search": float(self.settings.fetch_timeout_seconds + 5),
            },
            max_tokens=self.settings.model_dump().get("max_tokens"),
            agent_name=self.agent_def.name if self.agent_def else None,
            settings_snapshot=self.settings.model_dump(),
        )
        return Agent(
            options,
            system_prompt=self.system_prompt,
            tool_registry=self.tool_registry,
            session=self.session,
            event_bus=self.event_bus,
            workspace=self.workspace_ctx.workspace,
            llm=self.llm,
            compactor=self.compactor,
            run_id=self.run_id,
            extra_messages=self.config.extra_messages,
            resume=bool(self.config.resume_from or self.config.fork_from),
        )

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    async def run(self, prompt: str) -> RunResult:
        return await self._agent.run(prompt)

    def start(self, prompt: str) -> AgentHandle:
        return self._agent.start(prompt)
