"""``grep`` — search files for a regex. Uses ripgrep when available."""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path

from pydantic import BaseModel, Field

from pyharness import Tool, ToolContext, safe_path


class GrepArgs(BaseModel):
    pattern: str = Field(description="Regex pattern to search for.")
    path: str = Field(default=".", description="File or directory to search.")
    case_insensitive: bool = Field(default=False)
    max_results: int = Field(default=200, ge=1, le=10000)


class GrepTool(Tool):
    name = "grep"
    description = (
        "Search files for a regex. Returns `path:line:match`. Uses ripgrep "
        "when installed; falls back to Python regex over UTF-8 text files."
    )
    args_schema = GrepArgs

    async def execute(self, args: GrepArgs, ctx: ToolContext) -> str:  # type: ignore[override]
        target = safe_path(ctx.workspace, args.path)
        if not target.exists():
            return f"Path not found: {target}"

        rg = shutil.which("rg")
        if rg:
            cmd = [rg, "--no-heading", "--with-filename", "--line-number"]
            if args.case_insensitive:
                cmd.append("-i")
            cmd += ["-m", str(args.max_results), "--", args.pattern, str(target)]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await proc.communicate()
            stdout = stdout_b.decode("utf-8", errors="replace")
            if proc.returncode == 0 or proc.returncode == 1:
                lines = [ln for ln in stdout.splitlines() if ln]
                lines = lines[: args.max_results]
                if not lines:
                    return f"No matches for /{args.pattern}/ under {target}"
                return "\n".join(lines)
            return (
                f"ripgrep failed (rc={proc.returncode}): "
                f"{stderr_b.decode('utf-8', errors='replace')}"
            )

        return _python_grep(args, target)


def _python_grep(args: GrepArgs, target: Path) -> str:
    flags = re.IGNORECASE if args.case_insensitive else 0
    try:
        regex = re.compile(args.pattern, flags)
    except re.error as exc:
        return f"Invalid regex: {exc}"

    matches: list[str] = []
    files: list[Path]
    if target.is_file():
        files = [target]
    else:
        files = [p for p in target.rglob("*") if p.is_file()]

    for fp in files:
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                matches.append(f"{fp}:{i}:{line}")
                if len(matches) >= args.max_results:
                    return "\n".join(matches)

    if not matches:
        return f"No matches for /{args.pattern}/ under {target}"
    return "\n".join(matches)
