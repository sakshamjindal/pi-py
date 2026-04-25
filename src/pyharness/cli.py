"""Command-line entry point.

Implementation lands at Stage 9. The `main` function is wired up here so
that `pip install -e .` registers a working `pyharness` executable that
prints a clear "not yet implemented" message.
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    sys.stderr.write(
        "pyharness CLI is not yet implemented (Stage 9).\n"
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
