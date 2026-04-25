"""Minimal rich-formatted renderer for the pyharness coding agent.

This module is a *passive subscriber* to the event bus. It never
calls back into the SDK or harness layers — the loop runs identically
whether or not the TUI is attached. That preserves the headless-first
guarantee documented in DESIGN.md while giving an interactive caller
something nicer than the plain `pyharness` CLI's stderr trace.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from harness import CodingAgent, CodingAgentConfig


class TuiRenderer:
    """Subscribes to lifecycle events and prints them via rich."""

    def __init__(self, console: Console):
        self.console = console

    def attach(self, agent: CodingAgent) -> None:
        bus = agent.event_bus
        bus.subscribe("session_start", self._on_session_start)
        bus.subscribe("turn_start", self._on_turn_start)
        bus.subscribe("before_tool_call", self._on_tool_start)
        bus.subscribe("after_tool_call", self._on_tool_end)
        bus.subscribe("after_llm_call", self._on_llm)
        bus.subscribe("session_end", self._on_session_end)

    async def _on_session_start(self, event, ctx):
        model = event.payload.get("model", "?")
        self.console.print(
            Panel(f"[cyan]model:[/cyan] {model}", title="pyharness-tui", border_style="cyan")
        )
        return None

    async def _on_turn_start(self, event, ctx):
        turn = event.payload.get("turn")
        self.console.print(f"\n[dim]── turn {turn} ──────────────[/dim]")
        return None

    async def _on_tool_start(self, event, ctx):
        name = event.payload.get("tool_name", "?")
        args = event.payload.get("arguments") or {}
        hint = ""
        for v in args.values():
            if isinstance(v, str) and v:
                hint = v[:60].replace("\n", " ")
                break
        self.console.print(f"  [yellow]→[/yellow] [bold]{name}[/bold] [dim]{hint}[/dim]")
        return None

    async def _on_tool_end(self, event, ctx):
        ok = event.payload.get("ok")
        name = event.payload.get("tool_name", "?")
        ms = float(event.payload.get("duration_ms") or 0)
        if ok:
            self.console.print(f"    [green]✓[/green] {name} [dim]({ms:.0f}ms)[/dim]")
        else:
            self.console.print(f"    [red]✗[/red] {name}")
        return None

    async def _on_llm(self, event, ctx):
        resp = event.payload.get("response") or {}
        text = (resp.get("text") or "").strip()
        if text:
            self.console.print(Panel(text, border_style="blue"))
        return None

    async def _on_session_end(self, event, ctx):
        reason = event.payload.get("reason")
        if reason and reason != "completed":
            self.console.print(f"[red][end: {reason}][/red]")
        return None


async def run_tui(
    prompt: str,
    *,
    workspace: Path,
    model: str | None = None,
    bare: bool = False,
    console: Console | None = None,
) -> int:
    """Run a single coding-agent task with rich-formatted output."""

    console = console or Console()
    config = CodingAgentConfig(workspace=workspace, model=model, bare=bare)
    agent = CodingAgent(config)
    TuiRenderer(console).attach(agent)
    console.print(Panel(prompt, title="prompt", border_style="green"))
    result = await agent.run(prompt)
    console.print(
        Panel(
            (result.final_output or "").strip() or "(empty)",
            title="result",
            border_style="green",
        )
    )
    return 0 if result.completed else 1
