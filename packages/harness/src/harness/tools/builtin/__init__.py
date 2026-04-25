"""Built-in tool registry helpers."""

from __future__ import annotations

from pyharness import Tool, ToolRegistry
from .bash import BashTool
from .edit import EditTool
from .glob_tool import GlobTool
from .grep import GrepTool
from .read import ReadTool
from .web_fetch import WebFetchTool
from .web_search import WebSearchTool
from .write import WriteTool


def all_builtin_tools() -> list[Tool]:
    return [
        ReadTool(),
        WriteTool(),
        EditTool(),
        BashTool(),
        GrepTool(),
        GlobTool(),
        WebSearchTool(),
        WebFetchTool(),
    ]


def builtin_registry() -> ToolRegistry:
    reg = ToolRegistry()
    for t in all_builtin_tools():
        reg.register(t)
    return reg


def builtin_tool_names() -> list[str]:
    return [t.name for t in all_builtin_tools()]
