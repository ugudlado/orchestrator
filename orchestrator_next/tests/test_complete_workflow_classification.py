"""T-5 / T-6c: `complete-workflow` state-mutating classification.

`_STATE_MUTATING_INLINE_STEPS` in `bin/orchestrator` lists inline steps whose
script moves/deletes state.yaml as a side effect. Such steps must be
pre-recorded into state.yaml BEFORE their script runs, or `record.py` crashes
re-opening the now-moved file (the ORC-66 bug).

Two ids are state-mutating and must both be in the set:
  - `complete-workflow` — the terminal step for feature/bugfix; its archive
    phase `rm -rf`'s the state directory.
  - `archive-completed-change` — still the terminal step for the `spike`
    schema, which ORC-79 left untouched; its script `rm -rf`'s the state
    directory the same way and keeps its pre-record protection.
"""
from __future__ import annotations

import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_BIN = os.path.join(_REPO_ROOT, "bin", "orchestrator")


def _state_mutating_set():
    """Return the set of step ids in the `_STATE_MUTATING_INLINE_STEPS`
    assignment in bin/orchestrator, parsed from the source."""
    with open(_BIN) as f:
        source = f.read()
    m = re.search(
        r"_STATE_MUTATING_INLINE_STEPS\s*=\s*\{([^}]*)\}", source
    )
    assert m is not None, (
        "_STATE_MUTATING_INLINE_STEPS assignment not found in bin/orchestrator"
    )
    return set(re.findall(r'["\']([^"\']+)["\']', m.group(1)))


def test_set_contains_complete_workflow():
    steps = _state_mutating_set()
    assert "complete-workflow" in steps, (
        f"_STATE_MUTATING_INLINE_STEPS must contain 'complete-workflow', "
        f"got {steps}"
    )


def test_set_contains_archive_completed_change():
    """`archive-completed-change` stays state-mutating: spike.yaml still
    dispatches it as its terminal step, and its script `rm -rf`'s the state
    directory — so it keeps its pre-record crash protection."""
    steps = _state_mutating_set()
    assert "archive-completed-change" in steps, (
        "_STATE_MUTATING_INLINE_STEPS must contain 'archive-completed-change' "
        "— spike.yaml still dispatches it as a state-deleting inline step. "
        f"Got {steps}"
    )
