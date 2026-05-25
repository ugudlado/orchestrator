"""ORC-81: workflow_plan membership in reconcile FR-4 / FR-5."""
from __future__ import annotations

import duckdb
import pytest

from orchestrator_next.parser import State, StepHistoryEntry
from orchestrator_next.reconcile import reconcile_in_progress
from orchestrator_next.upsert import ensure_schema, upsert_pending_step_event


@pytest.fixture()
def in_memory_db():
    db = duckdb.connect(":memory:")
    ensure_schema(db)
    yield db
    db.close()


def _context() -> dict:
    return {"repo_root": "/repo/root", "change_id": "my-feature"}


def _plan() -> dict:
    return {
        "implement": {
            "nodes": [{"id": "preview-route"}, {"id": "execute-next-task"}],
        }
    }


class TestReconcileWorkflowPlanMembership:
    def test_fr4_strips_in_plan_ghost_when_db_present(self, in_memory_db):
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
        entry = StepHistoryEntry(
            step_id="ghost-step",
            phase="implement",
            status="in_progress",
            agent="developer",
            attempt=1,
            started_at="2024-01-01T00:00:00Z",
            ended_at=None,
            usage={},
            escalation=None,
            raw={"step_id": "ghost-step", "phase": "implement", "status": "in_progress"},
        )
        state = State(
            change_id="my-feature",
            phase="implement",
            repo_root="/repo/root",
            workflow_dir="/repo/root",
            workflow_plan=_plan(),
            step_history=[entry],
            raw={},
        )
        reconcile_in_progress(state, db, _context())
        assert state.step_history == []

    def test_fr5_skips_materialise_when_not_in_plan(self, in_memory_db):
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
            workflow_plan=_plan(),
            step_history=[],
            raw={},
        )
        reconcile_in_progress(state, db, _context())
        assert state.step_history == []

    def test_in_plan_in_progress_survives(self, in_memory_db):
        db = in_memory_db
        upsert_pending_step_event(
            db,
            repo_root="/repo/root",
            change_id="my-feature",
            phase="implement",
            step_id="preview-route",
            attempt=1,
            agent_name="developer",
            started_at="2024-01-01T00:00:00Z",
        )
        existing = StepHistoryEntry(
            step_id="preview-route",
            phase="implement",
            status="in_progress",
            agent="developer",
            attempt=1,
            started_at="2024-01-01T00:00:00Z",
            ended_at=None,
            usage={},
            escalation=None,
            raw={"step_id": "preview-route", "phase": "implement", "status": "in_progress"},
        )
        state = State(
            change_id="my-feature",
            phase="implement",
            repo_root="/repo/root",
            workflow_dir="/repo/root",
            workflow_plan=_plan(),
            step_history=[existing],
            raw={},
        )
        reconcile_in_progress(state, db, _context())
        assert len(state.step_history) == 1
        assert state.step_history[0].step_id == "preview-route"

    def test_legacy_active_list_shape(self, in_memory_db):
        db = in_memory_db
        upsert_pending_step_event(
            db,
            repo_root="/repo/root",
            change_id="my-feature",
            phase="implement",
            step_id="preview-route",
            attempt=1,
            agent_name="developer",
            started_at="2024-01-01T00:00:00Z",
        )
        good = StepHistoryEntry(
            step_id="preview-route",
            phase="implement",
            status="in_progress",
            agent="developer",
            attempt=1,
            started_at="2024-01-01T00:00:00Z",
            ended_at=None,
            usage={},
            escalation=None,
            raw={},
        )
        bad = StepHistoryEntry(
            step_id="ghost-step",
            phase="implement",
            status="in_progress",
            agent="developer",
            attempt=1,
            started_at="2024-01-01T00:00:00Z",
            ended_at=None,
            usage={},
            escalation=None,
            raw={},
        )
        state = State(
            change_id="my-feature",
            phase="implement",
            repo_root="/repo/root",
            workflow_dir="/repo/root",
            workflow_plan={"implement": {"active": ["preview-route"]}},
            step_history=[good, bad],
            raw={},
        )
        reconcile_in_progress(state, db, _context())
        assert [e.step_id for e in state.step_history] == ["preview-route"]
