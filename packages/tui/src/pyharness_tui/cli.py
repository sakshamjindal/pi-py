"""Entry point for the `pyharness-tui` console script."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from .app import run_tui


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pyharness-tui",
        description="Run a pyharness coding-agent task with a rich-formatted TUI.",
    )
    p.add_argument("prompt", nargs="*", help="Task prompt. Joined into one string.")
    p.add_argument("--workspace", type=Path, default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--bare", action="store_true", help="Skip extensions, AGENTS.md, settings.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    prompt = " ".join(args.prompt).strip()
    if not prompt:
        sys.stderr.write("error: no prompt provided.\n")
        return 2
    workspace = (args.workspace or Path.cwd()).resolve()
    return asyncio.run(
        run_tui(prompt, workspace=workspace, model=args.model, bare=args.bare)
    )


if __name__ == "__main__":
    raise SystemExit(main())
