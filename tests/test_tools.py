"""Tool system tests: registry, validation, builtins, hard-blocks."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from pyharness.tools.base import (
    Tool,
    ToolContext,
    ToolError,
    ToolRegistry,
    execute_tool,
)
from pyharness.tools.builtin import all_builtin_tools, builtin_registry, builtin_tool_names
from pyharness.tools.builtin.bash import BashTool, check_hard_blocks
from pyharness.tools.builtin.edit import EditTool
from pyharness.tools.builtin.glob_tool import GlobTool
from pyharness.tools.builtin.grep import GrepTool
from pyharness.tools.builtin.read import ReadTool
from pyharness.tools.builtin.write import WriteTool


def _ctx(workspace: Path) -> ToolContext:
    return ToolContext(workspace=workspace, session_id="s", run_id="r")


# ---------------------------------------------------------------------------
# Registry / validation
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Built-in tools
# ---------------------------------------------------------------------------


def test_builtins_count():
    names = builtin_tool_names()
    assert set(names) == {"read", "write", "edit", "bash", "grep", "glob", "web_search", "web_fetch"}


@pytest.mark.asyncio
async def test_read_and_write(tmp_path):
    ctx = _ctx(tmp_path)
    write_result = await execute_tool(WriteTool(), {"path": "a.txt", "content": "alpha\nbeta\n"}, ctx)
    assert write_result.ok
    read_result = await execute_tool(ReadTool(), {"path": "a.txt"}, ctx)
    assert read_result.ok
    assert "alpha" in read_result.content


@pytest.mark.asyncio
async def test_read_not_found(tmp_path):
    ctx = _ctx(tmp_path)
    result = await execute_tool(ReadTool(), {"path": "missing.txt"}, ctx)
    assert not result.ok


@pytest.mark.asyncio
async def test_edit_unique_replacement(tmp_path):
    ctx = _ctx(tmp_path)
    (tmp_path / "f.txt").write_text("hello world\n", encoding="utf-8")
    result = await execute_tool(
        EditTool(), {"path": "f.txt", "old_str": "world", "new_str": "there"}, ctx
    )
    assert result.ok
    assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "hello there\n"


@pytest.mark.asyncio
async def test_edit_zero_or_multiple_matches(tmp_path):
    ctx = _ctx(tmp_path)
    (tmp_path / "f.txt").write_text("foo foo\n", encoding="utf-8")
    res = await execute_tool(
        EditTool(), {"path": "f.txt", "old_str": "foo", "new_str": "bar"}, ctx
    )
    assert not res.ok
    res2 = await execute_tool(
        EditTool(), {"path": "f.txt", "old_str": "missing", "new_str": "x"}, ctx
    )
    assert not res2.ok


@pytest.mark.asyncio
async def test_bash_executes(tmp_path):
    ctx = _ctx(tmp_path)
    res = await execute_tool(BashTool(), {"command": "echo hi"}, ctx)
    assert res.ok
    assert "hi" in res.content


@pytest.mark.asyncio
async def test_bash_hard_block(tmp_path):
    ctx = _ctx(tmp_path)
    res = await execute_tool(BashTool(), {"command": "rm -rf /"}, ctx)
    assert res.ok
    assert "Blocked" in res.content


def test_bash_block_patterns():
    assert check_hard_blocks("rm -rf /") is not None
    assert check_hard_blocks("rm -rf ~/") is not None
    assert check_hard_blocks(":(){ :|:& };:") is not None
    assert check_hard_blocks("dd if=/dev/zero of=/dev/sda") is not None
    assert check_hard_blocks("mkfs.ext4 /dev/sda1") is not None
    assert check_hard_blocks("echo hi > /dev/sda") is not None
    assert check_hard_blocks("chmod -R 777 /") is not None
    assert check_hard_blocks("chown -R user /etc") is not None
    assert check_hard_blocks("ls -la") is None
    assert check_hard_blocks("rm -rf ./build") is None


@pytest.mark.asyncio
async def test_grep_python_fallback(tmp_path):
    ctx = _ctx(tmp_path)
    (tmp_path / "f.txt").write_text("hello\nworld\nhello again\n", encoding="utf-8")
    res = await execute_tool(GrepTool(), {"pattern": "hello", "path": "."}, ctx)
    assert res.ok
    assert "hello" in res.content


@pytest.mark.asyncio
async def test_glob(tmp_path):
    ctx = _ctx(tmp_path)
    (tmp_path / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "b.py").write_text("", encoding="utf-8")
    (tmp_path / "c.txt").write_text("", encoding="utf-8")
    res = await execute_tool(GlobTool(), {"pattern": "*.py"}, ctx)
    assert res.ok
    assert "a.py" in res.content and "b.py" in res.content
    assert "c.txt" not in res.content
