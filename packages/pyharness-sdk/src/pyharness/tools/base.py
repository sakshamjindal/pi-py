"""Tool ABC, registry, execution context, and the OpenAI-shape schema
generator used to advertise tools to the LLM.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ValidationError


@dataclass
class ToolContext:
    """Per-call execution context. Tools receive this on every invocation."""

    workspace: Path
    session_id: str
    run_id: str
    event_bus: Any = None  # set later (avoids import cycle with extensions)
    settings: Any = None
    extras: dict[str, Any] = field(default_factory=dict)


class ToolError(Exception):
    """Raised by tools on recoverable failure. The message becomes the tool
    result text the LLM sees, so it should be agent-actionable."""


class Tool(ABC):
    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    args_schema: ClassVar[type[BaseModel]]
    result_schema: ClassVar[type[BaseModel] | None] = None

    @abstractmethod
    async def execute(self, args: BaseModel, ctx: ToolContext) -> Any:
        """Run the tool. Return a Pydantic model, dict, or string. The
        registry serialises whatever you return into the tool result."""

    def to_openai_schema(self) -> dict[str, Any]:
        """Generate an OpenAI-format tool schema from ``args_schema``."""

        raw_schema = self.args_schema.model_json_schema()
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description.strip(),
                "parameters": _strip_schema(raw_schema),
            },
        }


def _strip_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Drop Pydantic-only annotations (``$defs``, ``title``) so the schema
    is what providers expect."""

    out = {k: v for k, v in schema.items() if k not in ("title",)}
    if "properties" in out and isinstance(out["properties"], dict):
        new_props: dict[str, Any] = {}
        for prop_name, prop_schema in out["properties"].items():
            if isinstance(prop_schema, dict):
                new_props[prop_name] = {k: v for k, v in prop_schema.items() if k != "title"}
            else:
                new_props[prop_name] = prop_schema
        out["properties"] = new_props
    return out


class ToolRegistry:
    """Holds the live set of tools the agent can call."""

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if not tool.name:
            raise ValueError("tool.name is required")
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def replace(self, name: str, tool: Tool) -> None:
        self._tools[name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def list_specs(self) -> list[dict[str, Any]]:
        return [t.to_openai_schema() for t in self._tools.values()]

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self):
        return iter(self._tools.values())


# ---------------------------------------------------------------------------
# Execution helpers
# ---------------------------------------------------------------------------


@dataclass
class ToolExecutionResult:
    ok: bool
    content: str
    error: str | None = None
    truncated: bool = False
    overflow_path: str | None = None
    raw_result: Any = None
    duration_ms: float = 0.0


async def execute_tool(
    tool: Tool,
    raw_args: dict[str, Any],
    ctx: ToolContext,
    *,
    timeout_seconds: float | None = None,
    max_bytes: int = 51_200,
    max_lines: int = 2000,
) -> ToolExecutionResult:
    """Validate args, run the tool, and post-process the result.

    Validation failures and tool exceptions are returned as ``ok=False``
    results so the loop can hand them to the LLM and let it retry without
    crashing the run.
    """

    started = asyncio.get_event_loop().time()

    try:
        args_obj = tool.args_schema.model_validate(raw_args)
    except ValidationError as exc:
        return ToolExecutionResult(
            ok=False,
            content=json.dumps(
                {
                    "error": "validation_failed",
                    "details": exc.errors(),
                }
            ),
            error="validation_failed",
            duration_ms=(asyncio.get_event_loop().time() - started) * 1000,
        )

    try:
        if timeout_seconds is not None:
            raw = await asyncio.wait_for(tool.execute(args_obj, ctx), timeout=timeout_seconds)
        else:
            raw = await tool.execute(args_obj, ctx)
    except ToolError as exc:
        return ToolExecutionResult(
            ok=False,
            content=str(exc),
            error="tool_error",
            duration_ms=(asyncio.get_event_loop().time() - started) * 1000,
        )
    except asyncio.TimeoutError:
        return ToolExecutionResult(
            ok=False,
            content=f"Tool {tool.name} timed out after {timeout_seconds}s",
            error="timeout",
            duration_ms=(asyncio.get_event_loop().time() - started) * 1000,
        )
    except Exception as exc:
        return ToolExecutionResult(
            ok=False,
            content=f"Tool {tool.name} raised {type(exc).__name__}: {exc}",
            error="exception",
            duration_ms=(asyncio.get_event_loop().time() - started) * 1000,
        )

    text = _stringify(raw)
    truncated = False
    overflow_path: str | None = None
    if len(text.encode("utf-8")) > max_bytes or text.count("\n") > max_lines:
        truncated = True
        overflow_path = _spill_to_disk(ctx, tool.name, text)
        text = _truncate(text, max_bytes, max_lines) + (
            f"\n\n[truncated; full output saved to {overflow_path}]"
        )

    return ToolExecutionResult(
        ok=True,
        content=text,
        truncated=truncated,
        overflow_path=overflow_path,
        raw_result=raw,
        duration_ms=(asyncio.get_event_loop().time() - started) * 1000,
    )


def _stringify(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, BaseModel):
        return raw.model_dump_json()
    try:
        return json.dumps(raw, default=str)
    except TypeError:
        return str(raw)


def _truncate(text: str, max_bytes: int, max_lines: int) -> str:
    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    out = "\n".join(lines)
    encoded = out.encode("utf-8")
    if len(encoded) > max_bytes:
        out = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return out


def _spill_to_disk(ctx: ToolContext, tool_name: str, text: str) -> str:
    spill_dir = Path(tempfile.gettempdir()) / "pyharness-overflow" / ctx.session_id
    spill_dir.mkdir(parents=True, exist_ok=True)
    path = spill_dir / f"{tool_name}-{uuid.uuid4().hex[:8]}.txt"
    path.write_text(text, encoding="utf-8")
    return str(path)


def safe_path(workspace: Path, raw_path: str) -> Path:
    """Resolve ``raw_path`` against the workspace. Absolute paths are
    accepted; relative paths are joined onto the workspace root."""

    p = Path(raw_path).expanduser()
    if not p.is_absolute():
        p = (workspace / p).resolve()
    else:
        p = p.resolve()
    return p
