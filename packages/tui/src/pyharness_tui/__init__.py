"""pyharness-tui — minimal REPL for dogfooding the coding agent.

Stdlib-only interactive shell. Loop behaviour is unaffected: the TUI
is a passive subscriber to the event bus (see DESIGN.md).
"""

from .cli import main

__version__ = "0.1.0"

__all__ = ["main", "__version__"]
