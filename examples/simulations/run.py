"""Friendly runner for the scenario simulations.

Wraps ``pytest.main()`` and prints a grouped summary at the end. Use
this when you want a digestible report instead of pytest's default
output. Pass ``--live`` (or set ``PYHARNESS_LIVE_API=1``) to include
the end-to-end scenarios that need a real Anthropic API key.

    $ python examples/simulations/run.py
    $ python examples/simulations/run.py --live

Returns nonzero if any scenario fails.
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
        help="Enable live (real-LLM) scenarios. Requires ANTHROPIC_API_KEY.",
    )
    parser.add_argument(
        "-k",
        default=None,
        help="Restrict to scenarios whose name matches this expression (pytest -k).",
    )
    parser.add_argument(
        "-v",
        action="store_true",
        help="Verbose pytest output.",
    )
    args = parser.parse_args(argv)

    if args.live:
        os.environ["PYHARNESS_LIVE_API"] = "1"
        if not os.environ.get("ANTHROPIC_API_KEY"):
            sys.stderr.write("WARNING: --live requested but ANTHROPIC_API_KEY is not set.\n")

    here = Path(__file__).resolve().parent
    pytest_args = [str(here), "--tb=short"]
    if args.v:
        pytest_args.append("-v")
    else:
        pytest_args.append("-q")
    if args.k:
        pytest_args.extend(["-k", args.k])

    rc = pytest.main(pytest_args)
    if rc == 0:
        sys.stdout.write("\nAll scenarios passed.\n")
    else:
        sys.stdout.write(
            f"\nSome scenarios failed (pytest exit code: {rc}).\nRe-run with -v for full output.\n"
        )
    return int(rc)


if __name__ == "__main__":
    raise SystemExit(main())
