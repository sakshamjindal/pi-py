"""The coding-agent assembly layer.

``CodingAgent`` is the closest analog to the old ``Harness`` class. It
takes a workspace + (optional) named agent + settings, walks AGENTS.md,
discovers skills, loads extensions, builds a tool registry, renders the
system prompt, and constructs a ``pyharness.Agent`` to run the loop.

The SDK kernel (``pyharness.Agent``) is dumb on purpose: it only knows
about messages, tools, sessions, events. Everything that makes
pyharness a coding agent — file conventions, named sub-agents, skills,
extension discovery, settings hierarchy — lives here.

Domain-specific harnesses (finance, autoresearch, ...) are built as
**project directories** with ``.pyharness/`` files that this layer
consumes. No subclassing needed. See
``docs/guides/build-finance-harness.md`` for the recipe.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date as _date
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
    Tool,
    ToolRegistry,
)

from .agents import discover_agents, load_agent_definition, resolve_tool_list
from .config import Settings
from .extensions_loader import discover_extensions, load_extensions
from .skills import LoadSkillTool, SkillDefinition, build_skill_index, discover_skills
from .tools.builtin import builtin_registry
from .workspace import WorkspaceContext

BASE_SYSTEM_PROMPT = (
    "You are an expert coding assistant operating inside pyharness, a coding "
    "agent harness. You help users by reading files, executing commands, "
    "editing code, and writing new files."
)


# Always-included guideline bullets, mirroring pi-mono's coding-agent
# defaults. The list is intentionally minimal — project-specific tone
# and policy belongs in ``AGENTS.md``, not here.
_BASE_GUIDELINES: tuple[str, ...] = (
    "Be concise in your responses",
    "Show file paths clearly when working with files",
)


def _short_snippet(description: str, max_len: int = 80) -> str:
    """Render a one-line tool snippet from a (possibly multi-paragraph)
    description. Takes the first sentence, collapses whitespace, drops
    trailing punctuation, and truncates."""

    text = " ".join(description.split())
    # First sentence boundary that isn't an abbreviation or decimal.
    for sep in (". ", ".\n"):
        idx = text.find(sep)
        if idx != -1:
            text = text[:idx]
            break
    text = text.rstrip(".").rstrip()
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text


def _format_tools_list(registry: ToolRegistry) -> str:
    lines = []
    for tool in registry:
        snippet = _short_snippet(tool.description or "")
        lines.append(f"- {tool.name}: {snippet}" if snippet else f"- {tool.name}")
    return "\n".join(lines) if lines else "(none)"


def _file_search_guideline(registry: ToolRegistry) -> str | None:
    """Pi-mono's conditional 'prefer specialised search tools over bash'
    bullet. Only emits when both are available."""

    has_bash = registry.has("bash")
    has_specialised = any(registry.has(name) for name in ("grep", "glob"))
    if has_bash and has_specialised:
        return (
            "Prefer grep/glob/read tools over bash for file exploration "
            "(faster, respects ignore rules, less context noise)"
        )
    return None


class NoProjectError(RuntimeError):
    """Raised when ``CodingAgent`` is constructed without a discoverable
    ``.pyharness/`` marker above the workspace and ``bare=False``.

    Pyharness requires an explicit project boundary so personal
    home-directory config can't silently leak into unrelated runs. Run
    ``pyharness init`` to create one, or pass ``bare=True`` to skip
    the project requirement entirely.
    """


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

    # Programmatic overlays for SDK / TUI use. Filesystem discovery still
    # runs; these are merged on top.
    extra_skills: list[SkillDefinition] = field(default_factory=list)
    extra_tools: list[Tool] = field(default_factory=list)
    extra_extensions: list[Callable[[ExtensionAPI], None]] = field(default_factory=list)

    # Allow/deny overrides. ``None`` means "fall back to the named agent's
    # frontmatter (or the relevant default)." A list — including the empty
    # list — overrides frontmatter and CLI flags.
    extensions_enabled: list[str] | None = None
    skills_enabled: list[str] | None = None


class CodingAgent:
    """Assembles a pyharness ``Agent`` with coding-agent defaults."""

    def __init__(self, config: CodingAgentConfig):
        self.config = config
        self.workspace_ctx = WorkspaceContext(
            workspace=config.workspace, project_root=config.project_root
        )
        if not config.bare and self.workspace_ctx.project_root is None:
            raise NoProjectError(
                f"No project found.\n\n"
                f"pyharness requires a `.pyharness/` directory at or above the workspace.\n"
                f"None was found above:\n  {self.workspace_ctx.workspace}\n\n"
                f"Either:\n"
                f"  - Run `pyharness init` from your project directory to create one, or\n"
                f"  - Use `--workspace <path>` to point inside an existing project, or\n"
                f"  - Pass `--bare` to skip the project requirement (no AGENTS.md, settings, or extensions)."
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

        # Build a closure that re-discovers skills on every call. This
        # makes ``load_skill`` see skills installed mid-run (e.g. via a
        # bash call to ``npx skills add ...``) without requiring the
        # user to restart the agent. The named-agent allowlist and
        # programmatic ``extra_skills`` are re-applied on every call,
        # so the contract is preserved.
        skill_allow = self._resolve_skills_allowlist()
        extra_skills = list(self.config.extra_skills)

        def _live_skills() -> dict[str, SkillDefinition]:
            found = discover_skills(self.workspace_ctx)
            if skill_allow is not None:
                found = {k: v for k, v in found.items() if k in skill_allow}
            for sd in extra_skills:
                found[sd.name] = sd
            return found

        # Initial snapshot for system prompt rendering. The system
        # prompt is built once at setup so this is the catalog the
        # model sees in its index. Newly-installed skills won't be
        # listed here, but the model can still reach them by name
        # (e.g. from the install tool's stdout).
        self.skills = _live_skills()

        # Programmatic always-on tools (extras win over duplicates).
        for tool in self.config.extra_tools:
            if self.tool_registry.has(tool.name):
                self.tool_registry.replace(tool.name, tool)
            else:
                self.tool_registry.register(tool)

        # Register the load_skill tool last so the registry already
        # contains the agent's other tools.
        self.load_skill_tool = LoadSkillTool(
            _live_skills, self.tool_registry, on_load=self._on_skill_loaded
        )
        self.tool_registry.register(self.load_skill_tool)
        self.system_prompt = self._build_system_prompt()

        # Extensions: opt-in only. Filesystem + entry-point discovery
        # always runs (so the catalog is queryable), but activation
        # requires explicit enable.
        self.extensions_available = discover_extensions(
            self.workspace_ctx.collect_extensions_dirs()
        )
        if self.config.bare:
            return

        ext_enabled = self._resolve_extensions_enabled()
        api = ExtensionAPI(
            bus=self.event_bus,
            registry=self.tool_registry,
            settings=self.settings,
            session_appender=None,
        )
        # Bind the API so skill bundles can register their hooks.py at
        # load_skill time.
        self.load_skill_tool.bind_extension_api(api)
        loaded = load_extensions(
            api,
            self.extensions_available,
            ext_enabled,
            extra_register_fns=self.config.extra_extensions,
        )
        self.extensions_loaded = loaded.modules

    # ------------------------------------------------------------------
    # Allowlist resolution (programmatic > frontmatter > default)
    # ------------------------------------------------------------------

    def _resolve_skills_allowlist(self) -> set[str] | None:
        """Return the set of skill names the agent may see.

        ``None`` means "no filter" (all discovered skills visible).
        """

        if self.config.skills_enabled is not None:
            allow = self.config.skills_enabled
        elif self.agent_def is not None:
            allow = list(self.agent_def.raw_frontmatter.get("skills") or [])
        else:
            allow = []
        if not allow or "*" in allow:
            return None
        return set(allow)

    def _resolve_extensions_enabled(self) -> list[str]:
        """Return the list of extension names to activate.

        Empty list means "no extensions activated." Extensions are never
        auto-loaded — they must be named explicitly somewhere.
        """

        if self.config.extensions_enabled is not None:
            return list(self.config.extensions_enabled)
        if self.agent_def is not None:
            return list(self.agent_def.raw_frontmatter.get("extensions") or [])
        return []

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
        """Assemble the system prompt in pi-mono's structure:

            1. Header (``BASE_SYSTEM_PROMPT``)
            2. ``Available tools:`` block — one line per registered tool
            3. ``Guidelines:`` block — base bullets + tool-conditional ones
            4. AGENTS.md content (already labelled per-file by the workspace)
            5. Named-agent body (frontmatter ``.md``)
            6. Skills index (``Available skills`` block)
            7. ``Current date:`` + ``Current working directory:`` footer

        Sections 4-6 are conditional on whether the harness has anything
        to put in them.
        """

        parts: list[str] = [BASE_SYSTEM_PROMPT.strip()]

        # 2. Available tools — gives the model an at-a-glance view without
        # forcing it to infer from JSON schemas. Mirrors pi-mono's pattern.
        parts.append(
            f"Available tools:\n{_format_tools_list(self.tool_registry)}\n\n"
            "In addition to the tools above, you may have access to "
            "other custom tools depending on the project."
        )

        # 3. Guidelines — base bullets plus tool-conditional ones. Kept short
        # by design; project policy goes in AGENTS.md.
        guidelines: list[str] = []
        bash_search = _file_search_guideline(self.tool_registry)
        if bash_search:
            guidelines.append(bash_search)
        guidelines.extend(_BASE_GUIDELINES)
        parts.append("Guidelines:\n" + "\n".join(f"- {g}" for g in guidelines))

        # 4. AGENTS.md (labelled per-file by ``render_agents_md``).
        if not self.config.bare:
            agents_md = self.workspace_ctx.render_agents_md()
            if agents_md:
                parts.append(agents_md.strip())

        # 5. Named-agent body.
        if self.agent_def is not None and self.agent_def.body.strip():
            parts.append(self.agent_def.body.strip())

        # 6. Skills catalog.
        skill_index = build_skill_index(self.skills)
        if skill_index:
            parts.append(skill_index.strip())

        # 7. Date + cwd footer. The model otherwise has no way to know
        # either, which matters for time-sensitive tasks and for resolving
        # relative paths.
        parts.append(
            f"Current date: {_date.today().isoformat()}\n"
            f"Current working directory: {self.workspace_ctx.workspace}"
        )

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
            tool_execution=self.settings.tool_execution,
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
