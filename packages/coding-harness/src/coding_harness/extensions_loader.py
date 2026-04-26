"""Filesystem loader for extension modules.

An extension is a Python module exposing a top-level ``register(api)``
function. This loader walks the configured extension directories, imports
each entry, and calls its ``register`` with the live ExtensionAPI. The
runtime types (EventBus, ExtensionAPI, HookOutcome, ...) live in
``pyharness.extensions``; this module only deals with file discovery.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pyharness import ExtensionAPI


@dataclass
class LoadedExtensions:
    modules: list[str] = field(default_factory=list)


def _import_path(path: Path, name_hint: str) -> Any | None:
    if path.is_dir():
        init = path / "__init__.py"
        if not init.is_file():
            return None
        target = init
    else:
        target = path
    spec_name = f"pyharness_ext_{name_hint}"
    spec = importlib.util.spec_from_file_location(spec_name, target)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.stderr.write(f"[extension] failed to load {target}: {exc}\n")
        return None
    return module


def load_extensions(
    api: ExtensionAPI,
    extension_dirs: list[Path],
) -> LoadedExtensions:
    """Walk extension directories and load each module's ``register(api)``.

    Project-local extensions override personal ones with the same name (we
    keep the last registration to win, matching the merge order: home,
    then project, then workspace).
    """

    by_name: dict[str, Path] = {}
    for d in extension_dirs:
        if not d.is_dir():
            continue
        for entry in sorted(d.iterdir()):
            if entry.name.startswith("_") or entry.name.startswith("."):
                continue
            if entry.is_file() and entry.suffix == ".py":
                by_name[entry.stem] = entry
            elif entry.is_dir() and (entry / "__init__.py").is_file():
                by_name[entry.name] = entry

    loaded = LoadedExtensions()
    for name, path in by_name.items():
        module = _import_path(path, name)
        if module is None:
            continue
        register = getattr(module, "register", None)
        if not callable(register):
            continue
        try:
            register(api)
        except Exception as exc:
            sys.stderr.write(f"[extension] {name} register() raised: {exc}\n")
            continue
        loaded.modules.append(name)
    return loaded
