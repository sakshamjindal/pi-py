"""Shared dynamic-import helper for tools modules and skill modules."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from .tools.base import Tool


def load_tools_from_module(path: Path) -> list[Tool]:
    """Import a `.py` file or package directory and return its `TOOLS` list.

    Modules are expected to expose a top-level ``TOOLS = [...]`` of
    ``Tool`` instances. Anything else is silently ignored — we don't want
    a malformed user module to crash discovery.
    """

    target = path / "__init__.py" if path.is_dir() else path
    if not target.is_file():
        return []
    spec_name = f"pyharness_dyn_{target.stem}_{abs(hash(str(target)))}"
    spec = importlib.util.spec_from_file_location(spec_name, target)
    if spec is None or spec.loader is None:
        return []
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        return []
    tools = getattr(module, "TOOLS", None)
    if not tools:
        return []
    return [t for t in tools if isinstance(t, Tool)]
