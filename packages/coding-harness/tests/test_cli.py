"""CLI smoke tests."""

from __future__ import annotations

from coding_harness import cli
from coding_harness.coding_agent import CodingAgent
from pyharness import LLMResponse


def test_cli_runs_with_mocked_llm(tmp_path, monkeypatch, isolated_session_dir, capsys):
    real_init = CodingAgent.__init__

    def patched_init(self, config):
        real_init(self, config)

        # Replace the LLM with a one-shot completion that returns "ok".
        async def _complete(**_):
            return LLMResponse(text="ok")

        self.llm.complete = _complete  # type: ignore

    monkeypatch.setattr(CodingAgent, "__init__", patched_init)
    monkeypatch.chdir(tmp_path)

    rc = cli.main(["--bare", "do something"])
    out = capsys.readouterr()
    assert rc == 0
    assert "ok" in out.out


def test_sessions_ls_no_sessions(tmp_path, monkeypatch, capsys, isolated_session_dir):
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["sessions", "ls"])
    err = capsys.readouterr().err
    assert rc == 0
    assert "(no sessions)" in err


def test_init_creates_pyharness_dir(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["init"])
    out = capsys.readouterr()
    assert rc == 0
    assert (tmp_path / ".pyharness").is_dir()
    assert (tmp_path / ".pyharness" / "settings.json").is_file()
    for sub in ("agents", "skills", "extensions", "tools"):
        assert (tmp_path / ".pyharness" / sub).is_dir()
    assert "Initialised pyharness project" in out.out


def test_init_refuses_overwrite_without_force(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    cli.main(["init"])  # first run
    rc = cli.main(["init"])  # second run without --force
    err = capsys.readouterr().err
    assert rc == 1
    assert "already exists" in err
    assert "--force" in err


def test_init_force_overwrites(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    cli.main(["init"])
    settings = tmp_path / ".pyharness" / "settings.json"
    settings.write_text("{}", encoding="utf-8")  # corrupt it
    rc = cli.main(["init", "--force"])
    assert rc == 0
    # Re-initialised content (not "{}").
    assert "default_model" in settings.read_text(encoding="utf-8")


def test_run_without_project_marker_fails_loudly(
    tmp_path, monkeypatch, capsys, isolated_session_dir
):
    """Running pyharness in a directory with no `.pyharness/` anywhere
    above must fail with rc=2 and a clear error pointing at `init`."""

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.chdir(scratch)

    rc = cli.main(["do something"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "No project found" in err
    assert "pyharness init" in err
    assert "--bare" in err
