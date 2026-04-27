"""Tiny ``.env`` loader with deterministic precedence.

We load ``.env`` from a small, predictable set of locations so
``pyharness init`` produces a workflow that Just Works after the user
copies ``.env.example`` to ``.env`` and fills in their key. No
``python-dotenv`` dependency — the format we accept is a strict subset
that covers the >99% case (``KEY=value`` lines, optional ``export``
prefix, optional surrounding quotes, ``#`` comments, blank lines).

Precedence (later sources do NOT override earlier ones):

1. Existing process environment — anything already exported in the shell wins.
2. ``<workspace>/.env`` — project-local secrets.
3. ``<project root>/.env`` — the directory containing ``.pyharness/``,
   walking upward from the workspace.
4. ``$PYHARNESS_HOME/.env`` (default: ``~/.pyharness/.env``) — personal
   keys shared across all projects.

This means a key exported in your shell always wins; a workspace
``.env`` overrides a personal one when the shell hasn't set it.

The loader never logs values (would expose secrets), only counts and
paths. Failures are silent — a malformed ``.env`` shouldn't break a
run.
"""

from __future__ import annotations

import os
from pathlib import Path


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a single ``.env`` file. Returns ``{}`` on any error."""

    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        eq = line.find("=")
        if eq <= 0:
            continue
        key = line[:eq].strip()
        if not key.replace("_", "").isalnum():
            continue
        value = line[eq + 1 :].strip()
        # Strip a trailing inline comment only if the value isn't quoted.
        if value and value[0] not in "\"'":
            hash_pos = value.find(" #")
            if hash_pos != -1:
                value = value[:hash_pos].rstrip()
        # Strip matching surrounding quotes.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def _candidate_paths(workspace: Path) -> list[Path]:
    paths: list[Path] = []
    # 1. workspace
    paths.append(workspace / ".env")
    # 2. project root (nearest ancestor with .pyharness/)
    cur: Path | None = workspace
    seen: set[Path] = set()
    while cur is not None and cur not in seen:
        seen.add(cur)
        if (cur / ".pyharness").is_dir():
            candidate = cur / ".env"
            if candidate not in paths:
                paths.append(candidate)
            break
        if cur.parent == cur:
            break
        cur = cur.parent
    # 3. personal home
    home_root = Path(os.environ.get("PYHARNESS_HOME", str(Path.home() / ".pyharness")))
    paths.append(home_root / ".env")
    return paths


def load_env(workspace: Path | None = None) -> list[Path]:
    """Load ``.env`` files into ``os.environ`` without overriding existing keys.

    Returns the list of paths that were actually read so callers can
    log them at ``--verbose`` if they want (we do not log them by
    default — a key being loaded from disk is not noteworthy unless
    something is wrong).
    """

    workspace = (workspace or Path.cwd()).resolve()
    loaded: list[Path] = []
    for path in _candidate_paths(workspace):
        if not path.is_file():
            continue
        for key, value in _parse_env_file(path).items():
            # Existing process env wins. If two .env files set the same
            # key, the first one we see (closest to workspace) wins.
            if key in os.environ:
                continue
            os.environ[key] = value
        loaded.append(path)
    return loaded
