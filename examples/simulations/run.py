"""Friendly runner for the scenario simulations.

    $ python examples/simulations/run.py            # mock-mode only
    $ python examples/simulations/run.py --live     # include live-LLM scenarios

Wraps pytest with a short summary. Live scenarios require
``OPENROUTER_API_KEY`` (or another LiteLLM-compatible provider) to be
set.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pytest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run.py")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Enable live-LLM scenarios. Requires OPENROUTER_API_KEY (or equivalent).",
    )
    parser.add_argument("-k", default=None, help="Restrict to scenarios matching expression.")
    parser.add_argument("-v", action="store_true", help="Verbose pytest output.")
    args = parser.parse_args(argv)

    if args.live:
        os.environ["PYHARNESS_LIVE_API"] = "1"
        if not (os.environ.get("OPENROUTER_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")):
            sys.stderr.write(
                "WARNING: --live requested but no OPENROUTER_API_KEY / ANTHROPIC_API_KEY in env.\n"
            )

    here = Path(__file__).resolve().parent
    pytest_args = [str(here), "--tb=short"]
    pytest_args.append("-v" if args.v else "-q")
    if args.k:
        pytest_args.extend(["-k", args.k])

    rc = pytest.main(pytest_args)
    sys.stdout.write(
        "\nAll scenarios passed.\n"
        if rc == 0
        else f"\nSome scenarios failed (pytest exit {rc}). Re-run with -v for details.\n"
    )
    return int(rc)


if __name__ == "__main__":
    raise SystemExit(main())
