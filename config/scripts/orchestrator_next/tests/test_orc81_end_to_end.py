"""ORC-81 end-to-end: ghost step_id absent from workflow_plan."""
from __future__ import annotations

import os
import sys

import duckdb
import pytest

from orchestrator_next.dispatch import dispatch
from orchestrator_next.parser import State, StepHistoryEntry
from orchestrator_next.reconcile import reconcile_in_progress
from orchestrator_next.upsert import ensure_schema, upsert_pending_step_event


@pytest.fixture()
def in_memory_db():
    db = duckdb.connect(":memory:")
    ensure_schema(db)
    yield db
    db.close()


def test_reconcile_drops_implement_pricing_module_ghost(in_memory_db):
    db = in_memory_db
    upsert_pending_step_event(
        db,
        repo_root="/repo",
        change_id="orc-81",
        phase="implement",
        step_id="implement-pricing-module",
        attempt=1,
        agent_name="developer",
        started_at="2024-01-01T00:00:00Z",
    )
    entry = StepHistoryEntry(
        step_id="implement-pricing-module",
        phase="implement",
        status="in_progress",
        agent="developer",
        attempt=1,
        started_at="2024-01-01T00:00:00Z",
        ended_at=None,
        usage={},
        escalation=None,
        raw={
            "step_id": "implement-pricing-module",
            "phase": "implement",
            "status": "in_progress",
            "attempt": 1,
        },
    )
    state = State(
        change_id="orc-81",
        phase="implement",
        repo_root="/repo",
        workflow_dir="/repo",
        workflow_plan={
            "implement": {"nodes": [{"id": "task-T-1"}, {"id": "preview-route"}]},
        },
        step_history=[entry],
        raw={},
    )
    reconcile_in_progress(state, db, {"repo_root": "/repo", "change_id": "orc-81"})
    assert not any(
        e.step_id == "implement-pricing-module" and e.status == "in_progress"
        for e in state.step_history
    )


def test_dispatch_exit_3_on_ghost_resume(tmp_path, capsys):
    state_path = tmp_path / "state.yaml"
    state_path.write_text("change_id: orc-81\nphase: implement\n")
    state = State(
        change_id="orc-81",
        phase="implement",
        repo_root="/repo",
        workflow_dir="/repo",
        workflow_plan={"implement": {"nodes": [{"id": "preview-route"}]}},
        step_history=[
            StepHistoryEntry(
                step_id="implement-pricing-module",
                phase="implement",
                status="in_progress",
                agent="developer",
                attempt=1,
                started_at="2024-01-01T00:00:00Z",
                ended_at=None,
                usage={},
                escalation=None,
                raw={
                    "step_id": "implement-pricing-module",
                    "phase": "implement",
                    "status": "in_progress",
                    "attempt": 1,
                },
            )
        ],
        raw={},
    )
    action, code = dispatch(state, str(state_path))
    assert action == {}
    assert code == 3
    err = capsys.readouterr().err
    assert "implement-pricing-module" in err
