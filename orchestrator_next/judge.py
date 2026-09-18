"""Optional TypeSafe judge — typed yes/no + choice answers over small state.

Hard fallback: every failure mode (env flag off, no API key, SDK not
installed, or the call itself raising) returns None. Callers treat None as
"judge unavailable" and take their existing, judge-free path — None is the
entire fallback switch, so a caller only ever needs `if r is None: ...`.

TypeSafe Python SDK: https://docs.typesafe.ai/sdk/python.md
"""
from __future__ import annotations

import os
import sys
from typing import Any


def enabled() -> bool:
    """True when the judge is configured and callable: env flag not 'off',
    TYPESAFE_API_KEY set, and typesafe_sdk importable."""
    if os.environ.get("ORCHESTRATOR_JUDGE", "").strip().lower() == "off":
        return False
    if not os.environ.get("TYPESAFE_API_KEY", "").strip():
        return False
    try:
        import typesafe_sdk  # noqa: F401
    except ImportError:
        return False
    return True


def choice(instructions: str, criteria: dict[str, str]) -> Any | None:
    """Build a TypeSafe Choice question, or None when the judge is
    unavailable (callers pass this straight to `ask`, which is a no-op on
    None questions — but never call SDK code when the SDK is not there)."""
    if not enabled():
        return None
    from typesafe_sdk import Choice

    return Choice(instructions=instructions, criteria=criteria)


def noul(instructions: str) -> Any | None:
    """Build a TypeSafe Noul (yes/no) question, or None when unavailable."""
    if not enabled():
        return None
    from typesafe_sdk import Noul

    return Noul(instructions=instructions)


def ask(state: dict, questions: dict) -> Any | None:
    """Ask the TypeSafe judge `questions` over `state`. Returns the SDK
    response, or None on any failure (see module docstring)."""
    if not enabled():
        return None
    try:
        from typesafe_sdk import TypeSafeClient

        with TypeSafeClient() as client:
            return client.system_one(state=state, questions=questions)
    except Exception as exc:  # noqa: BLE001 - judge is best-effort, never fatal
        sys.stderr.write(f"[judge] TypeSafe call failed, falling back: {exc}\n")
        return None
