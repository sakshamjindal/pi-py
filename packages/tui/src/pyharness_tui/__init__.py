"""pyharness-tui — minimal rich-formatted TUI for the coding agent.

Subscribes to the existing event bus and renders prompts, tool calls,
and final results with rich panels and colors. Loop behaviour is
unaffected — the TUI is a passive observer (see DESIGN.md).
"""

from .app import TuiRenderer, run_tui

__version__ = "0.1.0"

__all__ = ["TuiRenderer", "run_tui", "__version__"]
