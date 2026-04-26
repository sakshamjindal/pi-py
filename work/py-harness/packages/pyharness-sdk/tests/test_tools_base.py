"""Tool ABC + registry tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from pyharness import (
    Tool,
    ToolContext,
    ToolRegistry,
    execute_tool,
)


def _ctx(workspace: Path) -> ToolContext:
    return ToolContext(workspace=workspace, session_id="s", run_id="r")


class _EchoArgs(BaseModel):
    text: str = Field(description="What to echo")


class _EchoTool(Tool):
    name = "echo"
    description = "Echo a string."
    args_schema = _EchoArgs

    async def execute(self, args, ctx):
        return args.text


def test_registry_register_and_lookup():
    reg = ToolRegistry()
    reg.register(_EchoTool())
    assert reg.has("echo")
    assert reg.get("echo") is not None
    assert "echo" in reg.names()


def test_registry_rejects_duplicate():
    reg = ToolRegistry()
    reg.register(_EchoTool())
    with pytest.raises(ValueError):
        reg.register(_EchoTool())


def test_registry_replace_overrides():
    reg = ToolRegistry()
    reg.register(_EchoTool())
    reg.replace("echo", _EchoTool())
    assert reg.has("echo")


def test_to_openai_schema():
    schema = _EchoTool().to_openai_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "echo"
    assert "text" in schema["function"]["parameters"]["properties"]


@pytest.mark.asyncio
async def test_execute_validation_error_returns_result():
    result = await execute_tool(_EchoTool(), {}, _ctx(Path.cwd()))
    assert not result.ok
    assert result.error == "validation_failed"


@pytest.mark.asyncio
async def test_execute_happy_path():
    result = await execute_tool(_EchoTool(), {"text": "hi"}, _ctx(Path.cwd()))
    assert result.ok
    assert result.content == "hi"
