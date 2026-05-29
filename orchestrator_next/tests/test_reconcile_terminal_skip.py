"""
Regression tests for reconcile_in_progress FR-5 — Bug 2: stale DB in_progress
row materialized over a terminal YAML entry.

Root cause (reconcile.py, FR-5 block): yaml_keys is built only from
  e.status == "in_progress"
entries.  When state.yaml has a terminal entry (completed/failed) for
(phase, step_id, attempt) but the DB still holds an in_progress row for the
same triple, the triple is NOT in yaml_keys → FR-5 appends a new in_progress
entry.  Dispatch reads the tail, sees in_progress, and resumes the
just-completed step — duplicating work.

Fix: build yaml_keys from ALL step_history entries (any status), so terminal
entries block re-materialization of stale DB rows.

These tests FAIL on the current (unfixed) code.
"""
from __future__ import annotations

import pytest
import duckdb

from orchestrator_next.upsert import ensure_schema, upsert_pending_step_event
from orchestrator_next.parser import State, StepHistoryEntry
from orchestrator_next.reconcile import reconcile_in_progress


# ---------------------------------------------------------------------------
# Shared helpers (mirror test_reconcile_in_progress.py style)
# ---------------------------------------------------------------------------

@pytest.fixture()
def in_memory_db():
    db = duckdb.connect(":memory:")
    ensure_schema(db)
    yield db
    db.close()


def _make_state(step_history: list[StepHistoryEntry]) -> State:
    return State(
        change_id="my-feature",
        phase="main",
        repo_root="/repo/root",
        workflow_dir="/repo/root",
        workflow_plan={
            "main": {"nodes": [{"id": "execute-next-task"}]},
        },
        step_history=step_history,
        raw={},
    )


def _make_context() -> dict:
    return {"repo_root": "/repo/root", "change_id": "my-feature"}


def _terminal_entry(
    step_id: str,
    status: str,
    phase: str = "main",
    attempt: int = 1,
) -> StepHistoryEntry:
    return StepHistoryEntry(
        step_id=step_id,
        phase=phase,
        status=status,
        agent="developer",
        attempt=attempt,
        started_at="2024-01-01T00:00:00Z",
        ended_at="2024-01-01T01:00:00Z",
        usage={},
        escalation=None,
        raw={
            "step_id": step_id, "phase": phase, "status": status,
            "agent": "developer", "attempt": attempt,
            "started_at": "2024-01-01T00:00:00Z",
            "ended_at": "2024-01-01T01:00:00Z",
        },
    )


def _in_progress_entry(
    step_id: str,
    phase: str = "main",
    attempt: int = 1,
) -> StepHistoryEntry:
    return StepHistoryEntry(
        step_id=step_id,
        phase=phase,
        status="in_progress",
        agent="developer",
        attempt=attempt,
        started_at="2024-01-01T00:00:00Z",
        ended_at=None,
        usage={},
        escalation=None,
        raw={
            "step_id": step_id, "phase": phase, "status": "in_progress",
            "agent": "developer", "attempt": attempt,
            "started_at": "2024-01-01T00:00:00Z",
        },
    )


# ---------------------------------------------------------------------------
# Case 1 (FAILING on buggy code):
# YAML has a terminal "completed" entry for (main, execute-next-task, 1).
# DB still has an in_progress row for the same triple (stale row — DELETE was
# skipped because orchestrator done passes db=None).
# Expected (fixed):  reconcile does NOT append; step_history stays length 1,
#                    tail entry is "completed".
# Buggy behaviour:   yaml_keys misses the completed entry (filters in_progress
#                    only) → FR-5 appends in_progress → length becomes 2 →
#                    dispatch resumes the already-completed step.
# ---------------------------------------------------------------------------
def test_terminal_yaml_entry_blocks_db_in_progress_materialization(in_memory_db):
    """YAML completed + stale DB in_progress for same triple → no append.

    This is the ORC-45 step-advancement bug scenario.  Buggy FR-5 appends a
    duplicate in_progress entry, making step_history length 2 and the tail
    in_progress.  Fixed code includes terminal entries in yaml_keys, so the
    triple is already present and FR-5 skips materialization.
    """
    db = in_memory_db
    # Insert the stale DB row (simulates orchestrator done skipping the DELETE).
    upsert_pending_step_event(
        db,
        repo_root="/repo/root",
        change_id="my-feature",
        phase="main",
        step_id="execute-next-task",
        attempt=1,
        agent_name="developer",
        started_at="2024-01-01T00:00:00Z",
    )
    # YAML already has the completed terminal entry written by orchestrator done.
    completed = _terminal_entry(
        step_id="execute-next-task",
        status="completed",
        phase="main",
        attempt=1,
    )
    state = _make_state([completed])
    context = _make_context()

    reconcile_in_progress(state, db, context)

    # Bug: len == 2 and tail is in_progress → dispatch resumes.
    # Fix: len == 1 and entry is still completed → dispatch advances.
    assert len(state.step_history) == 1, (
        "FR-5 must NOT append an in_progress entry when YAML already has a "
        f"terminal entry for the same (phase, step_id, attempt) triple; "
        f"got {len(state.step_history)} entries: "
        f"{[(e.step_id, e.status) for e in state.step_history]}"
    )
    assert state.step_history[0].status == "completed", (
        "the terminal completed entry must remain as the sole (tail) entry"
    )


# ---------------------------------------------------------------------------
# Case 1b — failed terminal entry also blocks materialization.
# ---------------------------------------------------------------------------
def test_failed_yaml_entry_blocks_db_in_progress_materialization(in_memory_db):
    """YAML failed + stale DB in_progress for same triple → no append."""
    db = in_memory_db
    upsert_pending_step_event(
        db,
        repo_root="/repo/root",
        change_id="my-feature",
        phase="main",
        step_id="execute-next-task",
        attempt=1,
        agent_name="developer",
        started_at="2024-01-01T00:00:00Z",
    )
    failed = _terminal_entry(
        step_id="execute-next-task",
        status="failed",
        phase="main",
        attempt=1,
    )
    state = _make_state([failed])
    context = _make_context()

    reconcile_in_progress(state, db, context)

    assert len(state.step_history) == 1, (
        "FR-5 must NOT append in_progress when YAML has a failed entry for the same triple"
    )
    assert state.step_history[0].status == "failed"


# ---------------------------------------------------------------------------
# Case 2 (regression guard — passes on both buggy and fixed code):
# YAML is empty; DB has an in_progress row.
# Expected: reconcile appends the in_progress entry.
# This is the normal FR-5 materialization path — must continue to work.
# ---------------------------------------------------------------------------
def test_db_in_progress_materializes_when_yaml_empty(in_memory_db):
    """DB in_progress with no YAML entry → must be appended (normal FR-5 path).

    Regression guard: the fix must not break the legitimate materialization case.
    Passes on both buggy and fixed code.
    """
    db = in_memory_db
    upsert_pending_step_event(
        db,
        repo_root="/repo/root",
        change_id="my-feature",
        phase="main",
        step_id="execute-next-task",
        attempt=1,
        agent_name="developer",
        started_at="2024-01-01T00:00:00Z",
    )
    state = _make_state([])
    context = _make_context()

    reconcile_in_progress(state, db, context)

    assert len(state.step_history) == 1, (
        "DB-only in_progress row must be materialised when YAML has no entry for the triple"
    )
    assert state.step_history[0].status == "in_progress"
    assert state.step_history[0].step_id == "execute-next-task"
