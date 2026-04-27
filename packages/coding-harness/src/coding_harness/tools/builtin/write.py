"""``write`` — write a file, creating parents as needed."""

from __future__ import annotations

import contextlib

from pydantic import BaseModel, Field

from pyharness import FileMutationQueue, Tool, ToolContext, ToolError, safe_path


class WriteArgs(BaseModel):
    path: str = Field(description="Path to write. Parent dirs are created.")
    content: str = Field(description="Full file content.")


class WriteTool(Tool):
    name = "write"
    description = (
        "Write a file. Overwrites existing content. Parent directories are "
        "created. Returns the written path and line count."
    )
    args_schema = WriteArgs
    # No execution_mode override: writes to *different* files run in parallel.
    # Writes to the SAME file serialise via the per-path mutation queue.

    async def execute(self, args: WriteArgs, ctx: ToolContext) -> str:  # type: ignore[override]
        p = safe_path(ctx.workspace, args.path)

        queue: FileMutationQueue | None = ctx.extras.get("file_mutation_queue")
        guard = queue.acquire(p) if queue is not None else contextlib.nullcontext()

        async with guard:
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(args.content, encoding="utf-8")
            except OSError as exc:
                raise ToolError(f"Failed to write {p}: {exc}") from exc
            line_count = args.content.count("\n") + (
                0 if args.content.endswith("\n") or not args.content else 1
            )
            written = ctx.extras.setdefault("files_written", [])
            if str(p) not in written:
                written.append(str(p))
            return f"Wrote {line_count} lines to {p}"
