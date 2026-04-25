"""Settings merge across personal, project, and CLI overrides."""

from __future__ import annotations

import json
from pathlib import Path

from pyharness.config import Settings


def test_defaults():
    s = Settings()
    assert s.default_model == "claude-opus-4-7"
    assert s.max_turns == 100


def test_merge_personal_and_project_and_cli(tmp_path):
    home = tmp_path / "home"
    project = home / "p"
    workspace = project / "src"
    workspace.mkdir(parents=True)

    (home / ".pyharness").mkdir(parents=True)
    (home / ".pyharness" / "settings.json").write_text(
        json.dumps({"default_model": "personal-model", "max_turns": 50}),
        encoding="utf-8",
    )
    (project / ".pyharness").mkdir(parents=True)
    (project / ".pyharness" / "settings.json").write_text(
        json.dumps({"default_model": "project-model"}),
        encoding="utf-8",
    )

    s = Settings.load(workspace=workspace, home=home)
    assert s.default_model == "project-model"
    assert s.max_turns == 50

    s2 = Settings.load(
        workspace=workspace,
        home=home,
        cli_overrides={"default_model": "cli-model"},
    )
    assert s2.default_model == "cli-model"
