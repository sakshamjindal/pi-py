"""Circuit-breaker extension.

If the environment variable ``PYHARNESS_KILL_SWITCH`` is set, every LLM
call is denied. Use this for emergency stops in scheduled or unattended
runs without redeploying the harness.
"""

from __future__ import annotations

import os

from pyharness import ExtensionAPI, HookOutcome


def register(api: ExtensionAPI) -> None:
    api.on("before_llm_call", _check)


async def _check(event, ctx):
    if os.environ.get("PYHARNESS_KILL_SWITCH"):
        return HookOutcome.deny("PYHARNESS_KILL_SWITCH is set")
    return HookOutcome.cont()
