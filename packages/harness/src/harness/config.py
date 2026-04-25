"""Settings hierarchy: personal → project → CLI flags.

Settings are merged with later layers overriding earlier ones. The
workspace-level config is intentionally absent so that running pyharness
from any directory under a project uses the same project settings.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Settings(BaseModel):
    model_config = ConfigDict(extra="allow")

    default_model: str = "claude-opus-4-7"
    summarization_model: str = "claude-haiku-4-5"
    max_turns: int = 100
    compaction_threshold_pct: float = 0.8
    keep_recent_count: int = 20
    search_provider: str = "brave"
    search_api_key_env: str = "BRAVE_API_KEY"
    fetch_timeout_seconds: int = 30
    bash_timeout_seconds: int = 120
    tool_output_max_bytes: int = 51_200
    tool_output_max_lines: int = 2000
    session_dir: str = "~/.pyharness/sessions"
    fetch_allowlist: list[str] = Field(default_factory=list)
    fetch_blocklist: list[str] = Field(default_factory=list)
    model_context_window: int = 200_000

    @classmethod
    def load(
        cls,
        *,
        workspace: Path | None = None,
        project_root: Path | None = None,
        home: Path | None = None,
        cli_overrides: dict[str, Any] | None = None,
    ) -> "Settings":
        from .workspace import WorkspaceContext

        if workspace is None:
            workspace = Path.cwd()
        ctx = WorkspaceContext(workspace=workspace, project_root=project_root, home=home)
        merged: dict[str, Any] = {}
        for path in ctx.collect_settings_files():
            try:
                obj = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(obj, dict):
                merged = _deep_merge(merged, obj)
        if cli_overrides:
            merged = _deep_merge(merged, {k: v for k, v in cli_overrides.items() if v is not None})
        return cls.model_validate(merged)


def _deep_merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out
