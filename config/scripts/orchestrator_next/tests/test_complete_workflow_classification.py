"""T-5: `complete-workflow` state-mutating classification (RED before T-6).

`_STATE_MUTATING_INLINE_STEPS` in `bin/orchestrator` lists inline steps whose
script moves/deletes state.yaml as a side effect. Such steps must be
pre-recorded into state.yaml BEFORE their script runs, or `record.py` crashes
re-opening the now-moved file (the ORC-66 bug).

`complete-workflow` collapses archive + merge + worktree-removal into one
terminal step; archive (the `rm -rf`) is now a phase inside it. So
`complete-workflow` must be the sole entry in the set, and the former
`archive-completed-change` id — no longer a standalone dispatched step — must
be absent.
"""
from __future__ import annotations

import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))
_BIN = os.path.join(_REPO_ROOT, "bin", "orchestrator")


def _state_mutating_set():
    """Return the set of step ids in the `_STATE_MUTATING_INLINE_STEPS`
    assignment in bin/orchestrator, parsed from the source line."""
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


def test_set_excludes_archive_completed_change():
    steps = _state_mutating_set()
    assert "archive-completed-change" not in steps, (
        "_STATE_MUTATING_INLINE_STEPS must not contain "
        "'archive-completed-change' — it is no longer a standalone dispatched "
        f"step; archive is a phase inside complete-workflow. Got {steps}"
    )
