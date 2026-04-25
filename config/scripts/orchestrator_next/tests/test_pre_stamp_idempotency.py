"""
Regression test for the orphan in_progress row pattern.

Phase 4 retro item: when `orchestrator next` dispatches a step, it pre-stamps
an in_progress row in state.yaml.step_history. The original idempotency check
only looked for existing in_progress rows for (step_id, phase) — not for
terminal rows. If state.yaml had a transient inconsistency (e.g., an agent
recorded completion against a different working-directory path) and dispatch
re-issued the same step, the pre-stamp would orphan the prior completion.

The fix extends the idempotency check to skip the pre-stamp when a terminal
entry (completed / failed / escalate_to_architect / blocked) already exists
for (step_id, phase, attempt).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml


_REPO_ROOT = Path(__file__).resolve().parents[4]
_BIN_ORCHESTRATOR = _REPO_ROOT / "bin" / "orchestrator"


def _write_state_with_completed_step(tmp_path: Path) -> Path:
    """Build a state.yaml where 'explore' is already completed and the next
    pending step is 'design-and-draft-artifacts'. The plan.yaml lists both."""
    state = {
        "schema": "feature",
        "change_id": "demo",
        "slug": "demo",
        "status": "active",
        "repo_root": str(tmp_path),
        "phase": "specify",
        "next_step": {"phase": "specify", "step_id": "explore"},
        "workflow_plan": {
            "specify": {"active": ["explore", "design-and-draft-artifacts"], "filtered": []},
        },
        "step_history": [
            {
                "step_id": "explore",
                "phase": "specify",
                "status": "completed",
                "agent": "discoverer",
                "attempt": 1,
                "started_at": "2026-04-25T00:00:00Z",
                "ended_at": "2026-04-25T00:00:30Z",
                "usage": {"input_tokens": 100, "output_tokens": 10},
                "evidence": {"outputs": {"discovery_result": "x"}},
            }
        ],
        "flags": {},
    }
    state_path = tmp_path / "state.yaml"
    state_path.write_text(yaml.safe_dump(state))
    plan = {
        "phases": [
            {
                "name": "specify",
                "steps": [
                    {"id": "explore", "agent": "discoverer", "goal": "explore"},
                    {"id": "design-and-draft-artifacts", "agent": "architect", "goal": "design"},
                ],
            }
        ]
    }
    (tmp_path / "plan.yaml").write_text(yaml.safe_dump(plan))
    return state_path


def test_pre_stamp_does_not_orphan_completed_attempt(tmp_path):
    """If state.yaml already has a completed entry for (step_id, phase, attempt),
    the pre-stamp must not append a new in_progress row that orphans it."""
    state_path = _write_state_with_completed_step(tmp_path)

    # Run `orchestrator next` against the state. The dispatcher should pick the
    # next pending step (design-and-draft-artifacts) and pre-stamp it. The
    # already-completed 'explore' must not be re-stamped.
    env = {
        **os.environ,
        "WORKFLOW_STATE_DIR": str(tmp_path),
        "ORCHESTRATOR_HOME": str(_REPO_ROOT),
        "ORCHESTRATOR_REPO_ROOT": str(tmp_path),
    }
    result = subprocess.run(
        [sys.executable, str(_BIN_ORCHESTRATOR), "next", str(state_path)],
        capture_output=True,
        text=True,
        env=env,
    )

    # Re-read state.yaml.
    final_state = yaml.safe_load(state_path.read_text())
    history = final_state.get("step_history", [])

    # Count entries per (step_id, phase, status, attempt) tuple.
    explore_completed = [
        e for e in history
        if e.get("step_id") == "explore"
        and e.get("phase") == "specify"
        and e.get("status") == "completed"
        and e.get("attempt") == 1
    ]
    explore_in_progress = [
        e for e in history
        if e.get("step_id") == "explore"
        and e.get("phase") == "specify"
        and e.get("status") == "in_progress"
    ]

    assert len(explore_completed) == 1, (
        f"explore completed row should remain (got {len(explore_completed)}); "
        f"history={history!r}; stderr={result.stderr!r}"
    )
    assert len(explore_in_progress) == 0, (
        f"no in_progress row should be appended for already-completed explore "
        f"(got {len(explore_in_progress)}); history={history!r}"
    )


def test_pre_stamp_still_writes_for_new_step(tmp_path):
    """The fix must not break the normal pre-stamp path: a fresh pending step
    should still get an in_progress row appended."""
    state_path = _write_state_with_completed_step(tmp_path)
    env = {
        **os.environ,
        "WORKFLOW_STATE_DIR": str(tmp_path),
        "ORCHESTRATOR_HOME": str(_REPO_ROOT),
        "ORCHESTRATOR_REPO_ROOT": str(tmp_path),
    }
    subprocess.run(
        [sys.executable, str(_BIN_ORCHESTRATOR), "next", str(state_path)],
        capture_output=True,
        text=True,
        env=env,
    )

    final_state = yaml.safe_load(state_path.read_text())
    history = final_state.get("step_history", [])

    design_in_progress = [
        e for e in history
        if e.get("step_id") == "design-and-draft-artifacts"
        and e.get("phase") == "specify"
        and e.get("status") == "in_progress"
    ]
    assert len(design_in_progress) == 1, (
        f"new pending step should get exactly one in_progress row "
        f"(got {len(design_in_progress)}); history={history!r}"
    )
