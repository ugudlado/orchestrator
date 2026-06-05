"""T-5 / T-6c: `archive-completed-change` state-mutating classification.

`_STATE_MUTATING_INLINE_STEPS` in `orchestrator_next/run_loop` lists inline
steps whose script moves/deletes state.yaml as a side effect. Such steps must be
pre-recorded into state.yaml BEFORE their script runs, or `record.py` crashes
re-opening the now-moved file (the ORC-66 bug).

`archive-completed-change` is the terminal step for feature/bugfix/autopilot
and moves the active change directory into the archive path.
"""
from __future__ import annotations

import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_BIN = os.path.join(_REPO_ROOT, "bin", "orchestrator")


def _state_mutating_set():
    """Return the canonical `_STATE_MUTATING_INLINE_STEPS` set (imported, not
    source-grepped — the value lives in run_loop now)."""
    import sys
    sys.path.insert(0, os.path.join(_REPO_ROOT, "orchestrator_next", ".."))
    from orchestrator_next.run_loop import _STATE_MUTATING_INLINE_STEPS
    return set(_STATE_MUTATING_INLINE_STEPS)


def test_set_contains_archive_completed_change():
    steps = _state_mutating_set()
    assert steps == {"archive-completed-change"}, (
        f"_STATE_MUTATING_INLINE_STEPS must be exactly "
        f"{{'archive-completed-change'}}, got {steps}"
    )
