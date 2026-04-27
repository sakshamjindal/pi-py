"""``bash`` — execute shell commands with a hard-block list.

The hard-block list is conservative: only catastrophic-destruction
patterns. Everything else is allowed and runs with the user's permissions.
This is consistent with the v1 stance: "direct execution + hard-blocks;
defer sandbox providers."
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from pathlib import Path

from pydantic import BaseModel, Field

from pyharness import Tool, ToolContext, safe_path

# Patterns are checked against the raw command string. Each is a regex that
# matches "definitely catastrophic." Keep the list short — false positives
# are worse than false negatives here, since the agent will retry around
# any false positive and the user will see it.
_HARD_BLOCKS: list[tuple[str, re.Pattern[str]]] = [
    (
        "rm -rf /",
        re.compile(
            r"\brm\s+(?:-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*|-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*)\s+/(?:\s|$|\*|\.)"
        ),
    ),
    (
        "rm -rf ~",
        re.compile(
            r"\brm\s+(?:-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*|-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*)\s+~(?:/|\s|$)"
        ),
    ),
    (
        "rm -rf $HOME",
        re.compile(
            r"\brm\s+(?:-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*|-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*)\s+\$HOME\b"
        ),
    ),
    ("fork bomb", re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:")),
    ("dd to raw device", re.compile(r"\bdd\b[^|;]*\bof=/dev/(?:sd[a-z]|nvme\d|hd[a-z]|xvd[a-z])")),
    ("mkfs on device", re.compile(r"\bmkfs(?:\.\w+)?\s+/dev/(?:sd[a-z]|nvme\d|hd[a-z]|xvd[a-z])")),
    ("redirect to raw device", re.compile(r">\s*/dev/(?:sd[a-z]|nvme\d|hd[a-z]|xvd[a-z])")),
    ("chmod -R 777 /", re.compile(r"\bchmod\s+-R\s+0*777\s+/(?:\s|$)")),
    (
        "chown -R on system path",
        re.compile(r"\bchown\s+-R\b[^|;]*\s+/(?:\s|$|etc|usr|bin|sbin|var|lib)\b"),
    ),
]


def check_hard_blocks(command: str) -> str | None:
    for label, pattern in _HARD_BLOCKS:
        if pattern.search(command):
            return label
    return None


class BashArgs(BaseModel):
    command: str = Field(description="Shell command to execute.")
    cwd: str | None = Field(default=None, description="Working directory; defaults to workspace.")
    timeout: int = Field(default=120, ge=1, le=3600, description="Timeout in seconds.")


class BashTool(Tool):
    name = "bash"
    description = (
        "Execute a shell command. Defaults to the workspace as cwd. Output "
        "is captured (stdout+stderr) and truncated for large outputs. "
        "A small set of catastrophic patterns (e.g. `rm -rf /`) is blocked."
    )
    args_schema = BashArgs
    execution_mode = "sequential"

    async def execute(self, args: BashArgs, ctx: ToolContext) -> str:  # type: ignore[override]
        block = check_hard_blocks(args.command)
        if block is not None:
            return f"Blocked: command matches catastrophic destruction pattern ({block})."

        cwd_path: Path
        if args.cwd:
            cwd_path = safe_path(ctx.workspace, args.cwd)
        else:
            cwd_path = ctx.workspace
        if not cwd_path.exists() or not cwd_path.is_dir():
            return f"Error: cwd does not exist or is not a directory: {cwd_path}"

        proc = await asyncio.create_subprocess_shell(
            args.command,
            cwd=str(cwd_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=args.timeout)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            return f"Command timed out after {args.timeout}s: {args.command}"

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        rc = proc.returncode if proc.returncode is not None else -1

        parts = [f"$ {args.command}", f"[exit_code={rc}]"]
        if stdout:
            parts.append("--- stdout ---")
            parts.append(stdout)
        if stderr:
            parts.append("--- stderr ---")
            parts.append(stderr)
        return "\n".join(parts)
