"""``edit`` — single unique-occurrence string replacement."""

from __future__ import annotations

import contextlib

from pydantic import BaseModel, Field

from pyharness import FileMutationQueue, Tool, ToolContext, ToolError, safe_path


class EditArgs(BaseModel):
    path: str = Field(description="Path to the file to edit.")
    old_str: str = Field(description="Exact substring to replace. Must be unique.")
    new_str: str = Field(description="Replacement text.")


class EditTool(Tool):
    name = "edit"
    description = (
        "Replace a single unique occurrence of `old_str` with `new_str` in "
        "the given file. If `old_str` appears 0 or 2+ times, the call fails "
        "and you must add surrounding context to disambiguate."
    )
    args_schema = EditArgs
    # No execution_mode override: edits to *different* files run in parallel.
    # Edits to the SAME file serialise via the per-path mutation queue
    # below, which Agent injects into ToolContext.extras.

    async def execute(self, args: EditArgs, ctx: ToolContext) -> str:  # type: ignore[override]
        p = safe_path(ctx.workspace, args.path)

        queue: FileMutationQueue | None = ctx.extras.get("file_mutation_queue")
        # When no queue is present (older tests, alt embeddings), fall back
        # to a no-op context — same behaviour as before per-path locking.
        guard = queue.acquire(p) if queue is not None else contextlib.nullcontext()

        async with guard:
            if not p.exists() or not p.is_file():
                raise ToolError(f"File not found: {p}")

            original = p.read_text(encoding="utf-8")
            count = original.count(args.old_str)
            if count == 0:
                raise ToolError(
                    f"old_str not found in {p}. The text to replace must appear "
                    "verbatim, including whitespace."
                )
            if count > 1:
                raise ToolError(
                    f"old_str appears {count} times in {p}; it must be unique. "
                    "Add surrounding lines for context until exactly one match remains."
                )
            new_content = original.replace(args.old_str, args.new_str, 1)
            p.write_text(new_content, encoding="utf-8")

            written = ctx.extras.setdefault("files_written", [])
            if str(p) not in written:
                written.append(str(p))

            return f"Edited {p}: replaced 1 occurrence."
