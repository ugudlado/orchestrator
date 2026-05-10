#!/usr/bin/env python3
"""
Reproduction script for Bug 2: reconcile.py materializes stale in_progress entry
even when a terminal (completed) entry already exists for the same step.

Root cause: reconcile.py lines 86-112 builds yaml_keys from ONLY in_progress entries.
FR-5 materialization loop does not check for existing terminal entries (completed,
failed, etc.) for the same (phase, step_id, attempt). If the DB still holds an
in_progress row (because record.py ran without a DB connection so DELETE was skipped),
reconcile appends in_progress AFTER the completed entry. dispatch.py then sees
in_progress as the last step_history entry and resumes instead of advancing.

Expected: reconcile should not materialize in_progress when a terminal entry for
the same (phase, step_id, attempt) already exists in YAML.
Actual: reconcile appends in_progress unconditionally → dispatch resumes the step.

Run from any directory:
    python3 repro_bug2.py
"""
import sys
import os

# Import from the worktree to verify the fixed code
_ORC_ROOT = "/Users/spidey/code/feature_worktrees/orc-58"
sys.path.insert(0, os.path.join(_ORC_ROOT, "config", "scripts"))

from orchestrator_next.parser import StepHistoryEntry, State
from orchestrator_next.reconcile import reconcile_in_progress

class FakeDB:
    """Minimal DB stub that returns a single in_progress row for execute-next-task."""
    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        return self

    def fetchall(self):
        return self._rows


def make_entry(step_id, phase, status, attempt, agent="developer", started_at=None, ended_at=None):
    return StepHistoryEntry(
        step_id=step_id,
        phase=phase,
        status=status,
        agent=agent,
        attempt=attempt,
        started_at=started_at or "2026-05-10T10:00:00+00:00",
        ended_at=ended_at,
        usage={},
        escalation=None,
        raw={
            "step_id": step_id,
            "phase": phase,
            "status": status,
            "agent": agent,
            "attempt": attempt,
        },
    )


def main():
    # State: YAML has completed entry for execute-next-task attempt=1
    completed_entry = make_entry(
        step_id="execute-next-task",
        phase="main",
        status="completed",
        attempt=1,
        ended_at="2026-05-10T10:05:00+00:00",
    )

    state = State(
        change_id="orc-test",
        phase="main",
        repo_root="/tmp/fake-repo",
        workflow_dir="/tmp/fake-worktree",
        workflow_plan={},
        step_history=[completed_entry],
        raw={
            "change_id": "orc-test",
            "phase": "main",
            "repo_root": "/tmp/fake-repo",
        },
        complexity=None,
        worktree_artifact_dir="",
    )

    # DB has in_progress row for execute-next-task attempt=1 (stale — DELETE was skipped)
    # Row format: (phase, step_id, attempt, agent_name, started_at)
    stale_db_row = ("main", "execute-next-task", 1, "developer", "2026-05-10T10:00:00+00:00")
    fake_db = FakeDB([stale_db_row])

    context = {"repo_root": "/tmp/fake-repo", "change_id": "orc-test"}

    print("Before reconcile:")
    print(f"  step_history entries: {len(state.step_history)}")
    for e in state.step_history:
        print(f"    ({e.phase}, {e.step_id}, attempt={e.attempt}, status={e.status})")

    reconcile_in_progress(state, fake_db, context)

    print()
    print("After reconcile:")
    print(f"  step_history entries: {len(state.step_history)}")
    for e in state.step_history:
        print(f"    ({e.phase}, {e.step_id}, attempt={e.attempt}, status={e.status})")

    last = state.step_history[-1] if state.step_history else None
    print()
    if last and last.status == "in_progress":
        print("BUG 2 CONFIRMED: last entry is in_progress after reconcile!")
        print("dispatch.py will resume execute-next-task instead of advancing.")
    elif last and last.status == "completed":
        print("Bug NOT reproduced — last entry is completed (correct).")
    else:
        print(f"Unexpected last entry status: {last}")


if __name__ == "__main__":
    main()
