"""
Tests for reconcile_in_progress — FR-4 (yaml orphan strip) and FR-5 (DB materialise).

Scenarios:
  (a) YAML orphan stripped when DB empty: state has an in_progress entry;
      DB has zero in_progress rows. After reconcile, state.step_history no
      longer contains that entry.
  (b) DB row materialises yaml entry: DB has an in_progress row; state has
      no matching entry. After reconcile, state.step_history has a new
      in_progress entry with correct fields.
  (c) Matching on both sides preserved: both stores have the same
      (phase, step_id, attempt) in_progress. After reconcile, state.step_history
      is unchanged (no duplicate, no drop).
  (d) Non-in_progress entries untouched: state has completed, failed, and
      in_progress entries; DB has no rows. After reconcile, only the
      in_progress entry is stripped; completed and failed entries survive.
"""
from __future__ import annotations

import pytest
import duckdb

from orchestrator_next.upsert import ensure_schema, upsert_pending_step_event
from orchestrator_next.parser import State, StepHistoryEntry
from orchestrator_next.reconcile import reconcile_in_progress


@pytest.fixture()
def in_memory_db():
    db = duckdb.connect(":memory:")
    ensure_schema(db)
    yield db
    db.close()


def _make_state(step_history: list[StepHistoryEntry]) -> State:
    """Build a minimal State fixture for reconcile tests."""
    return State(
        change_id="my-feature",
        phase="implement",
        repo_root="/repo/root",
        workflow_dir="/repo/root",
        workflow_plan={
            "implement": {
                "nodes": [
                    {"id": "T-1"},
                    {"id": "T-2"},
                    {"id": "T-3"},
                    {"id": "T-4"},
                    {"id": "ghost-step"},
                    {"id": "preview-route"},
                ]
            }
        },
        step_history=step_history,
        raw={},
    )


def _make_context() -> dict:
    return {"repo_root": "/repo/root", "change_id": "my-feature"}


def _in_progress_entry(step_id: str, phase: str = "implement", attempt: int = 1) -> StepHistoryEntry:
    """Build a StepHistoryEntry with status='in_progress'."""
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


def _terminal_entry(step_id: str, status: str, phase: str = "implement", attempt: int = 1) -> StepHistoryEntry:
    """Build a StepHistoryEntry with a terminal status (completed or failed)."""
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


class TestReconcileInProgress:

    def test_yaml_orphan_stripped_when_db_empty(self, in_memory_db):
        """(a) in_progress entry in state with no matching DB row is stripped."""
        db = in_memory_db
        orphan = _in_progress_entry(step_id="T-1")
        state = _make_state([orphan])
        context = _make_context()

        reconcile_in_progress(state, db, context)

        assert len(state.step_history) == 0, (
            "orphan in_progress entry should be stripped when DB has no matching row"
        )

    def test_db_row_materialises_yaml_entry(self, in_memory_db):
        """(b) DB in_progress row with no matching state entry is appended to state."""
        db = in_memory_db
        upsert_pending_step_event(
            db,
            repo_root="/repo/root",
            change_id="my-feature",
            phase="implement",
            step_id="T-2",
            attempt=1,
            agent_name="developer",
            started_at="2024-01-01T00:00:00Z",
        )
        state = _make_state([])
        context = _make_context()

        reconcile_in_progress(state, db, context)

        assert len(state.step_history) == 1, (
            "DB-only in_progress row should be materialised into state.step_history"
        )
        entry = state.step_history[0]
        assert entry.step_id == "T-2"
        assert entry.phase == "implement"
        assert entry.status == "in_progress"
        assert entry.attempt == 1
        assert entry.agent == "developer"
        # DuckDB round-trips TIMESTAMP as datetime object, so str() may differ in format.
        assert entry.started_at is not None
        assert isinstance(entry.started_at, str)
        assert entry.ended_at is None
        assert entry.usage == {}
        assert entry.escalation is None

    def test_matching_on_both_sides_preserved(self, in_memory_db):
        """(c) Entry present in both DB and state is left unchanged (no dup, no drop)."""
        db = in_memory_db
        upsert_pending_step_event(
            db,
            repo_root="/repo/root",
            change_id="my-feature",
            phase="implement",
            step_id="T-3",
            attempt=1,
            agent_name="developer",
            started_at="2024-01-01T00:00:00Z",
        )
        existing = _in_progress_entry(step_id="T-3")
        state = _make_state([existing])
        context = _make_context()

        reconcile_in_progress(state, db, context)

        assert len(state.step_history) == 1, (
            "entry present in both DB and state must not be duplicated or dropped"
        )
        assert state.step_history[0].step_id == "T-3"
        assert state.step_history[0].status == "in_progress"

    def test_non_in_progress_entries_untouched(self, in_memory_db):
        """(d) completed and failed entries survive even when DB is empty."""
        db = in_memory_db
        completed = _terminal_entry(step_id="T-1", status="completed")
        failed = _terminal_entry(step_id="T-2", status="failed")
        orphan = _in_progress_entry(step_id="T-3")
        state = _make_state([completed, failed, orphan])
        context = _make_context()

        reconcile_in_progress(state, db, context)

        # Only the orphan in_progress should be stripped; 2 survivors remain.
        assert len(state.step_history) == 2, (
            "only the orphan in_progress should be stripped; completed and failed must survive"
        )
        statuses = {e.status for e in state.step_history}
        assert statuses == {"completed", "failed"}
        step_ids = {e.step_id for e in state.step_history}
        assert step_ids == {"T-1", "T-2"}

    def test_db_ghost_not_in_workflow_plan_is_not_materialised(self, in_memory_db):
        """DB in_progress row whose step_id is absent from workflow_plan is dropped."""
        db = in_memory_db
        upsert_pending_step_event(
            db,
            repo_root="/repo/root",
            change_id="my-feature",
            phase="implement",
            step_id="ghost-step",
            attempt=1,
            agent_name="developer",
            started_at="2024-01-01T00:00:00Z",
        )
        state = State(
            change_id="my-feature",
            phase="implement",
            repo_root="/repo/root",
            workflow_dir="/repo/root",
            workflow_plan={
                "implement": {
                    "nodes": [
                        {"id": "preview-route"},
                        {"id": "execute-next-task"},
                    ]
                }
            },
            step_history=[],
            raw={},
        )
        reconcile_in_progress(state, db, _make_context())

        assert state.step_history == []
