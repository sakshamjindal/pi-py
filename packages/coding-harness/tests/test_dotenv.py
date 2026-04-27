"""Tests for the .env auto-loader and `pyharness init` env scaffolding."""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from coding_harness.cli import _handle_init_cli
from coding_harness.dotenv import _parse_env_file, load_env

# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _write(path: Path, body: str) -> Path:
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def test_parse_basic_kv(tmp_path):
    f = _write(
        tmp_path / ".env",
        """\
        FOO=bar
        BAZ=qux
        """,
    )
    assert _parse_env_file(f) == {"FOO": "bar", "BAZ": "qux"}


def test_parse_strips_export_and_quotes(tmp_path):
    f = _write(
        tmp_path / ".env",
        """\
        export FOO="hello world"
        BAR='single quoted'
        BAZ=plain
        """,
    )
    assert _parse_env_file(f) == {
        "FOO": "hello world",
        "BAR": "single quoted",
        "BAZ": "plain",
    }


def test_parse_skips_comments_and_blanks(tmp_path):
    f = _write(
        tmp_path / ".env",
        """\
        # a comment
        FOO=bar

        # another
        BAZ=qux  # inline comment stripped
        """,
    )
    assert _parse_env_file(f) == {"FOO": "bar", "BAZ": "qux"}


def test_parse_keeps_inline_hash_inside_quoted_value(tmp_path):
    """An inline `#` inside a quoted value must NOT be treated as a comment."""

    f = _write(
        tmp_path / ".env",
        """\
        TOKEN="abc#123"
        """,
    )
    assert _parse_env_file(f) == {"TOKEN": "abc#123"}


def test_parse_returns_empty_on_missing_file(tmp_path):
    assert _parse_env_file(tmp_path / "does-not-exist.env") == {}


def test_parse_skips_malformed_lines(tmp_path):
    f = _write(
        tmp_path / ".env",
        """\
        validkey=ok
        =no-key-name
        not-an-assignment
        bad-key!=foo
        """,
    )
    # Only the well-formed line survives.
    assert _parse_env_file(f) == {"validkey": "ok"}


# ---------------------------------------------------------------------------
# load_env precedence
# ---------------------------------------------------------------------------


def test_load_env_does_not_override_existing_env(tmp_path, monkeypatch):
    """A key already exported in the shell must win over .env contents."""

    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write(workspace / ".env", "FOO=from_dotenv\n")

    monkeypatch.setenv("FOO", "from_shell")
    monkeypatch.setenv("PYHARNESS_HOME", str(tmp_path / "no-home"))
    load_env(workspace)
    assert os.environ["FOO"] == "from_shell"


def test_load_env_workspace_wins_over_personal(tmp_path, monkeypatch):
    """When the same key is in both ~/.pyharness/.env and the workspace,
    workspace wins (closer to the user's intent for this run)."""

    home = tmp_path / "home"
    home.mkdir()
    _write(home / ".env", "FOO=from_personal\n")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write(workspace / ".env", "FOO=from_workspace\n")

    monkeypatch.delenv("FOO", raising=False)
    monkeypatch.setenv("PYHARNESS_HOME", str(home))
    load_env(workspace)
    assert os.environ["FOO"] == "from_workspace"


def test_load_env_falls_through_to_personal_when_workspace_lacks_key(tmp_path, monkeypatch):
    """If a key is only in the personal .env, it should still be loaded."""

    home = tmp_path / "home"
    home.mkdir()
    _write(home / ".env", "PERSONAL_KEY=value\n")
    workspace = tmp_path / "ws"
    workspace.mkdir()  # no .env here

    monkeypatch.delenv("PERSONAL_KEY", raising=False)
    monkeypatch.setenv("PYHARNESS_HOME", str(home))
    load_env(workspace)
    assert os.environ["PERSONAL_KEY"] == "value"


def test_load_env_finds_project_root_above_workspace(tmp_path, monkeypatch):
    """If the workspace is a subdirectory of a project root that has a
    `.env`, the project-root `.env` is also loaded."""

    project = tmp_path / "project"
    (project / ".pyharness").mkdir(parents=True)
    _write(project / ".env", "PROJECT_KEY=here\n")
    workspace = project / "subdir"
    workspace.mkdir()

    monkeypatch.delenv("PROJECT_KEY", raising=False)
    monkeypatch.setenv("PYHARNESS_HOME", str(tmp_path / "no-home"))
    load_env(workspace)
    assert os.environ["PROJECT_KEY"] == "here"


# ---------------------------------------------------------------------------
# `pyharness init` env scaffolding
# ---------------------------------------------------------------------------


def test_init_creates_env_example_and_gitignore(tmp_path, capsys):
    rc = _handle_init_cli(["--path", str(tmp_path)])
    assert rc == 0

    example = tmp_path / ".env.example"
    assert example.is_file()
    content = example.read_text()
    # Sanity checks on the template.
    assert "OPENROUTER_API_KEY" in content
    assert "gitignored" in content

    gitignore = tmp_path / ".gitignore"
    assert gitignore.is_file()
    text = gitignore.read_text()
    assert ".env" in text
    assert ".env.*" in text


def test_init_does_not_clobber_existing_env_example(tmp_path):
    custom = "MY OWN TEMPLATE\n"
    (tmp_path / ".env.example").write_text(custom, encoding="utf-8")
    rc = _handle_init_cli(["--path", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / ".env.example").read_text() == custom


def test_init_extends_existing_gitignore_idempotently(tmp_path):
    (tmp_path / ".gitignore").write_text("# existing\nbuild/\n", encoding="utf-8")

    rc1 = _handle_init_cli(["--path", str(tmp_path)])
    assert rc1 == 0
    after_first = (tmp_path / ".gitignore").read_text()
    assert ".env" in after_first
    assert "build/" in after_first  # didn't clobber

    # Re-init must not duplicate the lines.
    rc2 = _handle_init_cli(["--path", str(tmp_path), "--force"])
    assert rc2 == 0
    after_second = (tmp_path / ".gitignore").read_text()
    assert after_second.count(".env\n") == 1
    assert after_second.count(".env.*") == 1


@pytest.mark.parametrize("preset", [".env", ".env.*"])
def test_init_does_not_add_pattern_already_in_gitignore(tmp_path, preset):
    (tmp_path / ".gitignore").write_text(preset + "\n", encoding="utf-8")
    rc = _handle_init_cli(["--path", str(tmp_path)])
    assert rc == 0
    text = (tmp_path / ".gitignore").read_text()
    assert text.count(preset + "\n") == 1
