"""Tests pinning the structure of the assembled system prompt.

The prompt mirrors pi-mono's coding-agent layout so behaviour stays
predictable across both harnesses. These tests don't assert on exact
wording — they assert on the *shape* (sections present, in the right
order, with the right contents) so guideline phrasing can drift
without breaking the suite.
"""

from __future__ import annotations

import re
from datetime import date as _date
from pathlib import Path

from coding_harness import CodingAgent, CodingAgentConfig, Settings
from coding_harness.coding_agent import (
    _BASE_GUIDELINES,
    _file_search_guideline,
    _format_tools_list,
    _short_snippet,
)
from pyharness import Tool, ToolRegistry


def _bare_project(tmp_path: Path) -> Path:
    """Minimal pyharness project: a directory with `.pyharness/` and a
    workspace inside it."""

    (tmp_path / ".pyharness").mkdir()
    (tmp_path / "src").mkdir()
    return tmp_path / "src"


def _agent(tmp_path: Path, **kwargs) -> CodingAgent:
    return CodingAgent(
        CodingAgentConfig(
            workspace=_bare_project(tmp_path),
            settings=Settings(),
            **kwargs,
        )
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class _FakeArgs:
    pass


def _fake_tool(name: str, description: str) -> Tool:
    """Quick Tool subclass for snippet-formatting tests; never executed."""

    cls = type(
        f"_T_{name}",
        (Tool,),
        {
            "name": name,
            "description": description,
            "args_schema": _FakeArgs,
            "execute": lambda self, args, ctx: None,
        },
    )
    return cls()


def test_short_snippet_takes_first_sentence():
    s = _short_snippet("Read a file. Returns its contents as a string.")
    assert s == "Read a file"


def test_short_snippet_collapses_whitespace_and_truncates():
    long = "Do a thing " * 20  # well over 80 chars, no period
    s = _short_snippet(long, max_len=40)
    assert len(s) <= 40
    assert "\n" not in s
    assert "  " not in s  # no doubled spaces


def test_format_tools_list_is_one_line_per_tool():
    reg = ToolRegistry()
    reg.register(_fake_tool("read", "Read a file. Returns contents."))
    reg.register(_fake_tool("bash", "Execute a shell command."))
    listing = _format_tools_list(reg)
    assert listing.splitlines() == [
        "- read: Read a file",
        "- bash: Execute a shell command",
    ]


def test_format_tools_list_handles_empty_registry():
    assert _format_tools_list(ToolRegistry()) == "(none)"


def test_file_search_guideline_emits_when_bash_and_specialised_present():
    reg = ToolRegistry()
    reg.register(_fake_tool("bash", "x"))
    reg.register(_fake_tool("grep", "x"))
    assert _file_search_guideline(reg) is not None


def test_file_search_guideline_silent_when_only_bash():
    reg = ToolRegistry()
    reg.register(_fake_tool("bash", "x"))
    assert _file_search_guideline(reg) is None


# ---------------------------------------------------------------------------
# end-to-end: CodingAgent's assembled prompt
# ---------------------------------------------------------------------------


def test_prompt_contains_pi_mono_style_sections(tmp_path):
    agent = _agent(tmp_path)
    prompt = agent.system_prompt

    # Header.
    assert prompt.startswith("You are an expert coding")

    # Available tools block, populated from the built-in registry.
    assert "Available tools:" in prompt
    assert re.search(r"(?m)^- bash:", prompt), "bash should appear in tools list"
    assert re.search(r"(?m)^- read:", prompt), "read should appear in tools list"

    # Guidelines block with at least the two always-on bullets.
    assert "Guidelines:" in prompt
    for bullet in _BASE_GUIDELINES:
        assert f"- {bullet}" in prompt

    # Footer with date + cwd.
    assert f"Current date: {_date.today().isoformat()}" in prompt
    assert f"Current working directory: {agent.workspace_ctx.workspace}" in prompt


def test_prompt_section_order_is_stable(tmp_path):
    """Section order matters for both readability and prompt caching:
    the deterministic prefix has to stay stable so cache hits accumulate
    across sessions."""

    agent = _agent(tmp_path)
    prompt = agent.system_prompt

    markers = [
        "Available tools:",
        "Guidelines:",
        "Current date:",
    ]
    indices = [prompt.index(m) for m in markers]
    assert indices == sorted(indices), (
        f"sections out of order: {list(zip(markers, indices, strict=True))}"
    )


def test_prompt_includes_skill_index_when_skills_present(tmp_path):
    # Create the project structure manually (the helper would race here).
    (tmp_path / ".pyharness" / "skills" / "demo").mkdir(parents=True)
    (tmp_path / "src").mkdir()
    (tmp_path / ".pyharness" / "skills" / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: A demo skill.\n---\n\nDemo body.\n",
        encoding="utf-8",
    )
    agent = CodingAgent(CodingAgentConfig(workspace=tmp_path / "src", settings=Settings()))
    prompt = agent.system_prompt
    assert "Available skills" in prompt
    assert "demo" in prompt


def test_bare_mode_skips_agents_md_but_keeps_tools_and_footer(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# Local guidance\nDo X always.\n", encoding="utf-8")
    agent = _agent(tmp_path, bare=True)
    prompt = agent.system_prompt

    # bare=True must not inline AGENTS.md.
    assert "Do X always" not in prompt
    # But the tools and footer are unconditional.
    assert "Available tools:" in prompt
    assert "Current date:" in prompt
