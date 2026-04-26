"""``read`` — read a file with line numbers, optional offset/limit."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from pyharness import Tool, ToolContext, ToolError, safe_path


class ReadArgs(BaseModel):
    path: str = Field(description="Path to the file to read.")
    offset: int = Field(default=0, ge=0, description="0-indexed starting line.")
    limit: int | None = Field(default=None, ge=1, description="Max lines to return.")


class ReadTool(Tool):
    name = "read"
    description = (
        "Read a UTF-8 text file. Returns line-numbered content. Use `offset` "
        "and `limit` for large files. Always succeed with absolute or "
        "workspace-relative paths."
    )
    args_schema = ReadArgs

    async def execute(self, args: ReadArgs, ctx: ToolContext) -> str:  # type: ignore[override]
        p: Path = safe_path(ctx.workspace, args.path)
        if not p.exists():
            raise ToolError(f"File not found: {p}")
        if not p.is_file():
            raise ToolError(f"Not a regular file: {p}")
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            raise ToolError(f"Failed to read {p}: {exc}") from exc

        lines = text.splitlines()
        end = len(lines) if args.limit is None else min(len(lines), args.offset + args.limit)
        out = []
        for i in range(args.offset, end):
            out.append(f"{i + 1:6d}\t{lines[i]}")
        if end < len(lines):
            out.append(
                f"\n[showing lines {args.offset + 1}-{end} of {len(lines)}; "
                f"call read again with offset={end} for more]"
            )
        return "\n".join(out) if out else f"[empty file: {p}]"
