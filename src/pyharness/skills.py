"""Skills: on-demand capability bundles loaded via the ``load_skill`` tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ._loader import load_tools_from_module
from .tools.base import Tool, ToolContext, ToolRegistry
from .workspace import WorkspaceContext


class SkillDefinition(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    description: str = ""
    tools: list[str] = Field(default_factory=list)
    body: str = ""
    raw_frontmatter: dict[str, Any] = Field(default_factory=dict)
    source_path: str | None = None
    tools_module: Path | None = None


def discover_skills(workspace: WorkspaceContext) -> dict[str, SkillDefinition]:
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
            sd = SkillDefinition(
                name=str(fm.get("name") or entry.name),
                description=str(fm.get("description") or ""),
                tools=list(fm.get("tools") or []),
                body=post.content or "",
                raw_frontmatter=fm,
                source_path=str(md),
                tools_module=tools_module,
            )
            found[sd.name] = sd
    return found


def build_skill_index(skills: dict[str, SkillDefinition]) -> str:
    if not skills:
        return ""
    lines = [
        "## Available skills",
        "",
        "Load a skill on demand by calling the `load_skill` tool with its "
        "name. After loading, the skill's instructions are returned and "
        "its tools become callable for the rest of the session.",
        "",
    ]
    for name, sd in sorted(skills.items()):
        desc = (sd.description or "").strip().split("\n", 1)[0]
        lines.append(f"- **{name}**: {desc}")
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
        skills: dict[str, SkillDefinition],
        registry: ToolRegistry,
        on_load: Any = None,
    ):
        self._skills = skills
        self._registry = registry
        self._on_load = on_load

    async def execute(self, args: _LoadSkillArgs, ctx: ToolContext):  # type: ignore[override]
        skill = self._skills.get(args.name)
        if skill is None:
            return LoadSkillResult(
                loaded=False,
                message=f"Unknown skill: {args.name!r}. Known: {sorted(self._skills.keys())}",
            )

        added: list[str] = []
        if skill.tools_module is not None:
            for tool in load_tools_from_module(skill.tools_module):
                if not self._registry.has(tool.name):
                    self._registry.register(tool)
                    added.append(tool.name)
                else:
                    self._registry.replace(tool.name, tool)

        if self._on_load is not None:
            try:
                await self._on_load(skill, added)
            except Exception:
                pass

        return LoadSkillResult(
            loaded=True,
            instructions=skill.body,
            tools_added=added,
            message=(
                f"Skill {skill.name!r} loaded. Tools now available: "
                f"{added or '(none new)'}. Read the returned instructions "
                "and proceed."
            ),
        )


