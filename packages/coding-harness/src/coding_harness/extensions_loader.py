"""Filesystem + entry-point discovery and loader for extensions.

An extension is a Python module exposing a top-level ``register(api)``
function. The loader has two phases:

1. ``discover_extensions(...)`` walks filesystem scopes and Python entry
   points to build a catalog of *available* extensions. No code is
   imported; only metadata.
2. ``load_extensions(api, available, enabled)`` imports the modules whose
   names appear in ``enabled`` and calls each module's ``register(api)``.

Extensions are **never auto-loaded.** The caller (``CodingAgent``)
decides which to enable based on the named agent's frontmatter, the
SDK's ``extra_extensions`` overlay, or a CLI flag.

The runtime types (``EventBus``, ``ExtensionAPI``, ``HookOutcome``) live
in ``pyharness.extensions``; this module only deals with discovery and
import.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pyharness import ExtensionAPI

# An extension reference is either:
#   - a filesystem Path to a `.py` file or package directory, or
#   - an importlib.metadata.EntryPoint (for pip-installed plugins).
ExtensionRef = Path | importlib.metadata.EntryPoint


@dataclass
class AvailableExtensions:
    """Catalog of extensions discovered on disk + entry points.

    Names are unique. Filesystem extensions are unprefixed; entry-point
    plugins are namespaced as ``<package>:<name>`` to avoid collisions
    across libraries.
    """

    refs: dict[str, ExtensionRef] = field(default_factory=dict)

    def names(self) -> list[str]:
        return sorted(self.refs.keys())


@dataclass
class LoadedExtensions:
    modules: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Discovery (no imports)
# ---------------------------------------------------------------------------


def discover_extensions(extension_dirs: list[Path]) -> AvailableExtensions:
    """Walk filesystem scopes + Python entry points. Pure metadata; nothing
    is imported."""

    refs: dict[str, ExtensionRef] = {}

    # Filesystem scopes — later scopes override earlier ones by name.
    for d in extension_dirs:
        if not d.is_dir():
            continue
        for entry in sorted(d.iterdir()):
            if entry.name.startswith("_") or entry.name.startswith("."):
                continue
            if entry.is_file() and entry.suffix == ".py":
                refs[entry.stem] = entry
            elif entry.is_dir() and (entry / "__init__.py").is_file():
                refs[entry.name] = entry

    # Python entry points — namespaced as `package:name`.
    try:
        eps = importlib.metadata.entry_points(group="pyharness.extensions")
    except Exception:
        eps = ()
    for ep in eps:
        package = ep.dist.name if ep.dist is not None else "unknown"
        refs[f"{package}:{ep.name}"] = ep

    return AvailableExtensions(refs=refs)


# ---------------------------------------------------------------------------
# Activation (imports + register())
# ---------------------------------------------------------------------------


def _import_filesystem(path: Path, name_hint: str) -> Any | None:
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


def _resolve_register(ref: ExtensionRef, name: str) -> Callable[[ExtensionAPI], None] | None:
    if isinstance(ref, Path):
        module = _import_filesystem(ref, name.replace(":", "_"))
        if module is None:
            return None
        register = getattr(module, "register", None)
        return register if callable(register) else None
    # Entry point — `ep.load()` returns whatever the value points at.
    try:
        loaded = ref.load()
    except Exception as exc:
        sys.stderr.write(f"[extension] entry point {name} failed to load: {exc}\n")
        return None
    if callable(loaded):
        return loaded
    register = getattr(loaded, "register", None)
    return register if callable(register) else None


def load_extensions(
    api: ExtensionAPI,
    available: AvailableExtensions | list[Path],
    enabled: Iterable[str] | None = None,
    *,
    extra_register_fns: Iterable[Callable[[ExtensionAPI], None]] = (),
) -> LoadedExtensions:
    """Activate the extensions named in ``enabled``.

    Backwards-compatibility: if ``available`` is a ``list[Path]`` (the
    old shape), it is treated as ``extension_dirs`` and discovery runs
    here. ``enabled=None`` in that case still means "load nothing" — the
    new opt-in default. Pass ``enabled=available.names()`` (or specific
    names) to actually activate.
    """

    if isinstance(available, list):
        available = discover_extensions(available)

    enabled_set: set[str] = set(enabled) if enabled is not None else set()

    loaded = LoadedExtensions()
    for name in sorted(enabled_set):
        ref = available.refs.get(name)
        if ref is None:
            sys.stderr.write(
                f"[extension] {name!r} requested but not found. "
                f"Known: {available.names()}\n"
            )
            continue
        register = _resolve_register(ref, name)
        if register is None:
            continue
        try:
            register(api)
        except Exception as exc:
            sys.stderr.write(f"[extension] {name} register() raised: {exc}\n")
            continue
        loaded.modules.append(name)

    # Programmatic register() callables (from CodingAgentConfig.extra_extensions).
    for register in extra_register_fns:
        try:
            register(api)
        except Exception as exc:
            sys.stderr.write(f"[extension] programmatic register() raised: {exc}\n")
            continue
        loaded.modules.append(getattr(register, "__name__", "<callable>"))

    return loaded
