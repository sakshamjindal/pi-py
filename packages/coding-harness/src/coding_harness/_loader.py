"""Shared dynamic-import helpers for tools modules, skill modules, and
skill-bundle hooks. Handles both filesystem paths (workspace/personal
scopes) and dotted module strings (entry-point plugins).
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pyharness import Tool


def _import_module(target: Path | str) -> Any | None:
    """Import a filesystem path OR a dotted module name. Returns None on
    any failure — we don't want a malformed user module to crash
    discovery or skill activation."""

    if isinstance(target, str):
        try:
            return importlib.import_module(target)
        except Exception:
            return None

    real = target / "__init__.py" if target.is_dir() else target
    if not real.is_file():
        return None
    spec_name = f"pyharness_dyn_{real.stem}_{abs(hash(str(real)))}"
    spec = importlib.util.spec_from_file_location(spec_name, real)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        return None
    return module


def load_tools_from_module(target: Path | str) -> list[Tool]:
    """Load and return the module-level ``TOOLS`` list of Tool instances."""

    module = _import_module(target)
    if module is None:
        return []
    tools = getattr(module, "TOOLS", None)
    if not tools:
        return []
    return [t for t in tools if isinstance(t, Tool)]


def load_register_from_module(target: Path | str) -> Callable[..., Any] | None:
    """Load and return the module-level ``register`` callable, or None."""

    module = _import_module(target)
    if module is None:
        return None
    register = getattr(module, "register", None)
    return register if callable(register) else None
