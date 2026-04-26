"""Tool subsystem: ABC, registry, execution helpers.

Built-in tools (read/write/edit/bash/...) live in the harness package.
The SDK only ships the protocol that any tool — built-in or not — must
satisfy.
"""

from __future__ import annotations

from .base import (
    Tool,
    ToolContext,
    ToolError,
    ToolExecutionResult,
    ToolRegistry,
    execute_tool,
    safe_path,
)

__all__ = [
    "Tool",
    "ToolContext",
    "ToolError",
    "ToolExecutionResult",
    "ToolRegistry",
    "execute_tool",
    "safe_path",
]
