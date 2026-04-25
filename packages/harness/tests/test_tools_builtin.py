"""Built-in tool tests: read/write/edit/bash/grep/glob, hard-blocks."""

from __future__ import annotations

from pathlib import Path

import pytest

from harness import builtin_tool_names
from harness.tools.builtin.bash import BashTool, check_hard_blocks
from harness.tools.builtin.edit import EditTool
from harness.tools.builtin.glob_tool import GlobTool
from harness.tools.builtin.grep import GrepTool
from harness.tools.builtin.read import ReadTool
from harness.tools.builtin.write import WriteTool
from pyharness import ToolContext, execute_tool


def _ctx(workspace: Path) -> ToolContext:
    return ToolContext(workspace=workspace, session_id="s", run_id="r")


def test_builtins_count():
    names = builtin_tool_names()
    assert set(names) == {
        "read",
        "write",
        "edit",
        "bash",
        "grep",
        "glob",
        "web_search",
        "web_fetch",
    }


@pytest.mark.asyncio
async def test_read_and_write(tmp_path):
    ctx = _ctx(tmp_path)
    write_result = await execute_tool(
        WriteTool(), {"path": "a.txt", "content": "alpha\nbeta\n"}, ctx
    )
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
    res = await execute_tool(EditTool(), {"path": "f.txt", "old_str": "foo", "new_str": "bar"}, ctx)
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
