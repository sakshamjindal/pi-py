"""``glob`` — list files matching a pathname pattern.

Module is named ``glob_tool`` to avoid shadowing the stdlib; the tool is
registered under the name ``glob``.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from ..base import Tool, ToolContext, safe_path


class GlobArgs(BaseModel):
    pattern: str = Field(description="Glob pattern, e.g. `**/*.py`.")
    cwd: str | None = Field(default=None, description="Directory to search; defaults to workspace.")
    max_results: int = Field(default=500, ge=1, le=10000)


class GlobTool(Tool):
    name = "glob"
    description = (
        "List files matching a glob pattern. Supports `**` for recursive "
        "matches. Returns paths relative to the search root."
    )
    args_schema = GlobArgs

    async def execute(self, args: GlobArgs, ctx: ToolContext) -> str:  # type: ignore[override]
        root: Path = safe_path(ctx.workspace, args.cwd) if args.cwd else ctx.workspace
        if not root.exists():
            return f"Path not found: {root}"
        if not root.is_dir():
            return f"Not a directory: {root}"

        # Path.glob already handles `**` patterns when used with rglob-style,
        # but to keep behaviour predictable across Python versions we use
        # Path.glob directly with the literal pattern.
        results = []
        for p in root.glob(args.pattern):
            try:
                rel = p.relative_to(root)
            except ValueError:
                rel = p
            results.append(str(rel))
            if len(results) >= args.max_results:
                break
        results.sort()
        if not results:
            return f"No matches for pattern {args.pattern!r} under {root}"
        return "\n".join(results)
