"""Tool execution edge cases.

Covers the failure paths in ``pyharness.tools.base.execute_tool``:
validation, ToolError, generic exceptions, timeout, large-output
truncation, and the ``_stringify`` fallback for non-JSON results.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from pyharness import (
    Tool,
    ToolContext,
    ToolError,
    execute_tool,
)


class _NumArgs(BaseModel):
    n: int = Field(description="A positive integer.")


class _RaisingTool(Tool):
    name = "raises"
    description = "Always raises the configured exception."
    args_schema = _NumArgs

    def __init__(self, exc: BaseException):
        self._exc = exc

    async def execute(self, args, ctx):
        raise self._exc


class _SleepingTool(Tool):
    name = "sleep"
    description = "Sleeps the requested number of seconds."
    args_schema = _NumArgs

    async def execute(self, args, ctx):
        await asyncio.sleep(args.n)
        return "done"


class _BigTool(Tool):
    name = "big"
    description = "Returns a large block of text."
    args_schema = _NumArgs

    async def execute(self, args, ctx):
        # 60 KiB > default max_bytes of 51200.
        return "A" * args.n


class _NonSerialisableTool(Tool):
    name = "weird"
    description = "Returns an object that json cannot serialise."
    args_schema = _NumArgs

    async def execute(self, args, ctx):
        # set is not JSON-native; _stringify falls back to str().
        return {1, 2, 3}


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        workspace=tmp_path,
        session_id="sim",
        run_id="r1",
    )


@pytest.mark.asyncio
async def test_pydantic_validation_failure(tmp_path):
    """Wrong type for `n` -> ok=False, error='validation_failed'."""

    tool = _RaisingTool(RuntimeError("never reached"))
    result = await execute_tool(tool, {"n": "not an int"}, _ctx(tmp_path))
    assert result.ok is False
    assert result.error == "validation_failed"
    payload = json.loads(result.content)
    assert payload["error"] == "validation_failed"
    assert any("int" in str(d).lower() for d in payload["details"])


@pytest.mark.asyncio
async def test_tool_raises_tool_error(tmp_path):
    tool = _RaisingTool(ToolError("agent-readable failure reason"))
    result = await execute_tool(tool, {"n": 1}, _ctx(tmp_path))
    assert result.ok is False
    assert result.error == "tool_error"
    assert "agent-readable failure reason" in result.content


@pytest.mark.asyncio
async def test_tool_raises_generic_exception(tmp_path):
    tool = _RaisingTool(ValueError("boom"))
    result = await execute_tool(tool, {"n": 1}, _ctx(tmp_path))
    assert result.ok is False
    assert result.error == "exception"
    assert "ValueError" in result.content
    assert "boom" in result.content


@pytest.mark.asyncio
async def test_tool_timeout(tmp_path):
    tool = _SleepingTool()
    result = await execute_tool(
        tool,
        {"n": 5},
        _ctx(tmp_path),
        timeout_seconds=0.1,
    )
    assert result.ok is False
    assert result.error == "timeout"
    assert "timed out" in result.content


@pytest.mark.asyncio
async def test_large_output_is_truncated_and_spilled(tmp_path, monkeypatch):
    """Output larger than ``max_bytes`` is truncated; the full content is
    spilled to disk and the path is appended to the result."""

    monkeypatch.setenv("PYHARNESS_SESSION_DIR", str(tmp_path / "sessions"))
    tool = _BigTool()
    result = await execute_tool(
        tool,
        {"n": 60_000},
        _ctx(tmp_path),
        max_bytes=1024,
    )
    assert result.ok is True
    assert result.truncated is True
    assert result.overflow_path is not None
    # The spilled file exists and contains the full output.
    spill = Path(result.overflow_path)
    assert spill.is_file()
    assert spill.stat().st_size >= 60_000


@pytest.mark.asyncio
async def test_non_serialisable_result_falls_back_to_str(tmp_path):
    """A ``set`` is not JSON-serialisable; ``_stringify`` falls back to
    ``str()`` so the loop never crashes on a tool's return value."""

    tool = _NonSerialisableTool()
    result = await execute_tool(tool, {"n": 1}, _ctx(tmp_path))
    assert result.ok is True
    # Output is the str() of the set.
    assert "1" in result.content and "2" in result.content
