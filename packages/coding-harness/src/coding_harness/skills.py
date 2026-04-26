"""Skills: on-demand capability bundles loaded via the ``load_skill`` tool."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from pyharness import Tool, ToolContext, ToolRegistry

from ._loader import load_tools_from_module
from .workspace import WorkspaceContext


class SkillDefinition(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    description: str = ""
    tools: list[str] = Field(default_factory=list)
    body: str = ""
    raw_frontmatter: dict[str, Any] = Field(default_factory=dict)
    source_path: str | None = None
    # ``tools_module`` is either a filesystem Path to a tools.py / tools/
    # package, or a dotted Python module path for entry-point plugins.
    tools_module: Path | str | None = None
    # ``hooks_module`` is an optional colocated hooks.py whose register()
    # is invoked when the skill is activated via load_skill.
    hooks_module: Path | str | None = None


def discover_skills(workspace: WorkspaceContext) -> dict[str, SkillDefinition]:
    """Walk filesystem scopes + Python entry points.

    No skill code is imported here. ``tools.py`` and ``hooks.py`` paths
    are recorded; modules are imported only when ``load_skill`` activates
    the skill. Filesystem scopes apply most-general-first so later scopes
    override earlier ones by name.
    """

    import importlib.metadata

    import frontmatter

    found: dict[str, SkillDefinition] = {}
    for d in workspace.collect_skills_dirs():
        if not d.is_dir():
            continue
        for entry in sorted(d.iterdir()):
            if not entry.is_dir():
                continue
            md = entry / "SKILL.md"
            if not md.is_file():
                continue
            post = frontmatter.load(str(md))
            fm = dict(post.metadata or {})
            tools_module: Path | None = None
            tp = entry / "tools.py"
            if tp.is_file():
                tools_module = tp
            elif (entry / "tools" / "__init__.py").is_file():
                tools_module = entry / "tools"
            hooks_module: Path | None = None
            hp = entry / "hooks.py"
            if hp.is_file():
                hooks_module = hp
            sd = SkillDefinition(
                name=str(fm.get("name") or entry.name),
                description=str(fm.get("description") or ""),
                tools=list(fm.get("tools") or []),
                body=post.content or "",
                raw_frontmatter=fm,
                source_path=str(md),
                tools_module=tools_module,
                hooks_module=hooks_module,
            )
            found[sd.name] = sd

    # Python entry points: pip-installed packages may publish skills
    # without writing files into ~/.pyharness. They are namespaced as
    # ``<package>:<name>`` so plain filesystem names cannot collide.
    try:
        eps = importlib.metadata.entry_points(group="pyharness.skills")
    except Exception:
        eps = ()
    for ep in eps:
        package = ep.dist.name if ep.dist is not None else "unknown"
        try:
            target = ep.load()
        except Exception:
            continue
        # Entry points may yield either a SkillDefinition directly, or a
        # dotted module path string that points at a tools module.
        if isinstance(target, SkillDefinition):
            sd = target
            if not sd.name.startswith(f"{package}:"):
                sd = sd.model_copy(update={"name": f"{package}:{ep.name}"})
        else:
            # Treat the entry-point value as a tools module reference.
            sd = SkillDefinition(
                name=f"{package}:{ep.name}",
                description=ep.dist.metadata.get("Summary", "") if ep.dist else "",
                tools_module=ep.value,
                source_path=f"entry_point:{package}",
            )
        found[sd.name] = sd
    return found


def build_skill_index(
    skills: dict[str, SkillDefinition],
    loaded: set[str] | None = None,
) -> str:
    """Render the skill catalog as a `<system-reminder>` block.

    Skills already loaded in the current session are listed under a
    "Loaded" header so the model knows not to call ``load_skill`` again
    for them. Available (unloaded) skills are listed below.
    """

    if not skills:
        return ""
    loaded = loaded or set()

    avail = sorted((n, sd) for n, sd in skills.items() if n not in loaded)
    active = sorted((n, sd) for n, sd in skills.items() if n in loaded)

    lines = ["<system-reminder>"]
    if active:
        lines.append("Loaded skills (already active, do not call load_skill again):")
        for name, _sd in active:
            lines.append(f"- {name}")
        lines.append("")
    if avail:
        lines.append(
            "Available skills. Call `load_skill` with a skill name to activate it; "
            "the skill's instructions are returned and its tools become callable."
        )
        for name, sd in avail:
            desc = (sd.description or "").strip().split("\n", 1)[0]
            lines.append(f"- {name}: {desc}")
    lines.append("</system-reminder>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# load_skill built-in tool
# ---------------------------------------------------------------------------


class _LoadSkillArgs(BaseModel):
    name: str = Field(description="Name of the skill to load.")


class LoadSkillResult(BaseModel):
    loaded: bool
    instructions: str = ""
    tools_added: list[str] = Field(default_factory=list)
    message: str = ""


class LoadSkillTool(Tool):
    name = "load_skill"
    description = (
        "Load a skill into the current session. Use this when one of the "
        "skills listed in the system prompt's 'Available skills' section "
        "matches the user's request. After loading, the skill's tools "
        "are callable and its instructions are returned to you."
    )
    args_schema = _LoadSkillArgs
    result_schema = LoadSkillResult

    def __init__(
        self,
        skills: dict[str, SkillDefinition] | Callable[[], dict[str, SkillDefinition]],
        registry: ToolRegistry,
        on_load: Any = None,
    ):
        # ``skills`` may be either a fixed dict (the historical shape;
        # tests still pass dicts) or a 0-arg callable that returns the
        # current skill dict on every invocation. The callable form
        # enables live re-discovery so that a skill installed mid-run
        # (e.g. via ``npx skills add ...`` from a bash tool call) becomes
        # loadable without restarting the agent.
        if callable(skills):
            self._provider: Callable[[], dict[str, SkillDefinition]] = skills
        else:
            _frozen = skills
            self._provider = lambda: _frozen
        self._registry = registry
        self._on_load = on_load
        # Names of skills that have been loaded in this session. Memoized
        # here so that prompt re-renders and the TUI can show accurate
        # loaded/available state without scanning session events.
        self.loaded_names: set[str] = set()

    @property
    def _skills(self) -> dict[str, SkillDefinition]:
        """Backwards-compatible accessor — returns the current skills dict."""

        return self._provider()

    async def execute(self, args: _LoadSkillArgs, ctx: ToolContext):  # type: ignore[override]
        skills = self._provider()  # live re-walk on each call
        skill = skills.get(args.name)
        if skill is None:
            return LoadSkillResult(
                loaded=False,
                message=f"Unknown skill: {args.name!r}. Known: {sorted(skills.keys())}",
            )

        added: list[str] = []
        if skill.tools_module is not None:
            for tool in load_tools_from_module(skill.tools_module):
                if not self._registry.has(tool.name):
                    self._registry.register(tool)
                    added.append(tool.name)
                else:
                    self._registry.replace(tool.name, tool)

        # Run any colocated `hooks.py` register() if this skill is a
        # bundle. Imported here to avoid a top-level cycle.
        if skill.hooks_module is not None:
            self._run_skill_hooks(skill)

        self.loaded_names.add(skill.name)

        if self._on_load is not None:
            with contextlib.suppress(Exception):
                await self._on_load(skill, added)

        return LoadSkillResult(
            loaded=True,
            instructions=skill.body,
            tools_added=added,
            message=(
                f"Skill {skill.name!r} loaded. Tools now available: "
                f"{added or '(none new)'}. Do not call load_skill({skill.name!r}) "
                "again. Read the returned instructions and proceed."
            ),
        )

    def _run_skill_hooks(self, skill: SkillDefinition) -> None:
        """Activate the skill's colocated hooks.py via its register(api)."""

        from ._loader import load_register_from_module

        api = getattr(self, "_extension_api", None)
        if api is None:
            return
        register = load_register_from_module(skill.hooks_module)
        if register is None:
            return
        with contextlib.suppress(Exception):
            register(api)

    def bind_extension_api(self, api: Any) -> None:
        """Attach the live ExtensionAPI so skill bundles can register hooks
        when their skill is loaded. Called by ``CodingAgent`` at setup."""

        self._extension_api = api
