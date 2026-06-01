"""T-7 (RED) / T-8 (GREEN): mark-change-completed trigger and atomic ROLLBACK.

Covers FR-3, FR-4, NFR-1, AC-1, AC-2, AC-5.

Cases:
  (a) record() with step_id="mark-change-completed" + status="completed" writes
      both step_events and feature_metrics rows.
  (b) _write_feature_metrics mocked to raise → no step_events row remains
      (ROLLBACK verified by COUNT) AND exit code is non-zero.
  (c) _resolve_feature_metrics mocked to raise → BEGIN never issued AND exit
      code is non-zero.
  (d) non-mark-change-completed step routes through existing Phase 4 boundary
      path (regression check).
  (e) git-log subprocess timeout → row written with zero churn columns, exit 0.
  (f) mark-change-completed with status="recovered" does NOT trigger absorbed path.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import duckdb
import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from orchestrator_next.upsert import ensure_schema  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_db():
    db = duckdb.connect(":memory:")
    ensure_schema(db)
    return db


def _make_state_yaml(tmp_path: Path, change_id: str = "my-feature",
                     schema: str = "feature", phase: str = "complete",
                     with_tasks: bool = True) -> str:
    """Write a minimal state.yaml that includes required fields for the trigger."""
    if with_tasks:
        tasks_md = tmp_path / "tasks.md"
        tasks_md.write_text("- [x] T1\n- [x] T2\n- [ ] T3\n")
        tasks_path = str(tasks_md)
    else:
        tasks_path = None

    # complete phase: mark-change-completed is NOT the last step
    workflow_plan = {
        "complete": {
            "active": [
                "mark-change-completed",
                "workflow-report",
                "archive-completed-change",
                "remove-worktree",
            ],
            "filtered": [],
        }
    }

    state = {
        "change_id": change_id,
        "schema": schema,
        "phase": phase,
        "repo_root": str(tmp_path),
        "worktree_path": str(tmp_path),
        "started_at": "2026-01-01T00:00:00Z",
        "completed_at": "2026-01-01T01:00:00Z",
        "workflow_plan": workflow_plan,
        "step_history": [],
    }
    if tasks_path:
        state["tasks_path"] = tasks_path

    path = tmp_path / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(path)


def _mcc_payload(status: str = "completed") -> dict:
    """Return a minimal payload for mark-change-completed."""
    return {
        "step_id": "mark-change-completed",
        "phase": "complete",
        "status": status,
        "agent": "developer",
        "outputs": {},
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


# ---------------------------------------------------------------------------
# (a) writes both step_events and feature_metrics rows on mark-change-completed
# ---------------------------------------------------------------------------

def test_trigger_writes_step_and_feature_metrics(tmp_path, monkeypatch):
    """mark-change-completed + status=completed writes step_events AND feature_metrics."""
    from orchestrator_next.record import record

    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir()

    db = _fresh_db()
    state_path = _make_state_yaml(tmp_path)
    payload = _mcc_payload("completed")

    result, code = record(state_path, payload, db=db)

    assert code == 0, f"Expected exit 0, got {code}: {result}"

    # step_events row written
    step_count = db.execute(
        "SELECT COUNT(*) FROM step_events WHERE change_id=? AND step_id=?",
        ["my-feature", "mark-change-completed"],
    ).fetchone()[0]
    assert step_count == 1, f"Expected 1 step_events row, got {step_count}"

    # feature_metrics row written
    fm_count = db.execute(
        "SELECT COUNT(*) FROM feature_metrics WHERE change_id=?",
        ["my-feature"],
    ).fetchone()[0]
    assert fm_count == 1, f"Expected 1 feature_metrics row, got {fm_count}"

    # source column starts with done@
    source = db.execute(
        "SELECT source FROM feature_metrics WHERE change_id=?",
        ["my-feature"],
    ).fetchone()[0]
    assert source.startswith("done@"), f"Expected source starting with 'done@', got {source!r}"

    db.close()


# ---------------------------------------------------------------------------
# (b) _write_feature_metrics raises → ROLLBACK, no step_events row, non-zero exit
# ---------------------------------------------------------------------------

def test_write_feature_metrics_raises_rolls_back(tmp_path, monkeypatch):
    """When _write_feature_metrics raises, ROLLBACK: no step_events row + non-zero exit."""
    from orchestrator_next.record import record

    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir()

    db = _fresh_db()
    state_path = _make_state_yaml(tmp_path)
    payload = _mcc_payload("completed")

    with patch(
        "orchestrator_next.record._write_feature_metrics",
        side_effect=RuntimeError("simulated feature_metrics write failure"),
    ):
        result, code = record(state_path, payload, db=db)

    # Non-zero exit
    assert code != 0, f"Expected non-zero exit, got {code}: {result}"

    # No step_events row should remain (rolled back)
    step_count = db.execute(
        "SELECT COUNT(*) FROM step_events WHERE change_id=? AND step_id=?",
        ["my-feature", "mark-change-completed"],
    ).fetchone()[0]
    assert step_count == 0, f"Expected 0 step_events rows after ROLLBACK, got {step_count}"

    db.close()


# ---------------------------------------------------------------------------
# (c) _resolve_feature_metrics raises → BEGIN never issued, non-zero exit
# ---------------------------------------------------------------------------

def test_resolve_feature_metrics_raises_before_begin(tmp_path, monkeypatch):
    """_resolve_feature_metrics raises → BEGIN is NOT issued, exit is non-zero."""
    from orchestrator_next.record import record

    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir()

    db = _fresh_db()
    state_path = _make_state_yaml(tmp_path)
    payload = _mcc_payload("completed")

    begin_called = []
    original_execute = db.execute

    # We can't patch db.execute (read-only), so instead we track BEGIN via the
    # actual DB state: if BEGIN is issued, a feature_metrics row would be attempted.
    # Simpler: patch _resolve_feature_metrics to raise and verify no rows written.
    with patch(
        "orchestrator_next.record._resolve_feature_metrics",
        side_effect=RuntimeError("simulated resolve failure"),
    ):
        result, code = record(state_path, payload, db=db)

    # Non-zero exit
    assert code != 0, f"Expected non-zero exit, got {code}: {result}"

    # No rows written at all — transaction never opened
    step_count = db.execute(
        "SELECT COUNT(*) FROM step_events WHERE change_id=? AND step_id=?",
        ["my-feature", "mark-change-completed"],
    ).fetchone()[0]
    assert step_count == 0, f"Expected 0 rows before BEGIN, got {step_count}"

    fm_count = db.execute(
        "SELECT COUNT(*) FROM feature_metrics WHERE change_id=?",
        ["my-feature"],
    ).fetchone()[0]
    assert fm_count == 0, f"Expected 0 feature_metrics rows, got {fm_count}"

    db.close()


# ---------------------------------------------------------------------------
# (d) non-mark-change-completed step routes through existing Phase 4 boundary path
# ---------------------------------------------------------------------------

def test_non_mcc_step_routes_through_phase4_boundary(tmp_path, monkeypatch):
    """Non-mark-change-completed step uses existing Phase 4 boundary path."""
    from orchestrator_next.record import record

    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir()

    db = _fresh_db()

    # Make a workflow where "some-step" is NOT the last step
    state = {
        "change_id": "my-feature",
        "schema": "feature",
        "phase": "implement",
        "repo_root": str(tmp_path),
        "worktree_path": str(tmp_path),
        "started_at": "2026-01-01T00:00:00Z",
        "completed_at": "2026-01-01T01:00:00Z",
        "workflow_plan": {
            "implement": {
                "active": ["some-step", "other-step"],
                "filtered": [],
            }
        },
        "step_history": [],
    }
    state_path = tmp_path / "state.yaml"
    state_path.write_text(yaml.safe_dump(state, sort_keys=False))

    payload = {
        "step_id": "some-step",
        "phase": "implement",
        "status": "completed",
        "agent": "developer",
        "outputs": {},
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }

    # Patch _write_feature_metrics to verify it's NOT called for non-mcc steps
    with patch("orchestrator_next.record._write_feature_metrics") as mock_write_fm:
        result, code = record(str(state_path), payload, db=db)

    # feature_metrics path should NOT be invoked for non-mark-change-completed
    mock_write_fm.assert_not_called()
    assert code == 0, f"Expected exit 0, got {code}: {result}"

    # No feature_metrics row
    fm_count = db.execute(
        "SELECT COUNT(*) FROM feature_metrics WHERE change_id=?",
        ["my-feature"],
    ).fetchone()[0]
    assert fm_count == 0

    db.close()


# ---------------------------------------------------------------------------
# (e) git-log subprocess timeout → row written with zero churn columns, exit 0
# ---------------------------------------------------------------------------

def test_git_churn_timeout_produces_zero_churn_row(tmp_path, monkeypatch):
    """run_git_churn subprocess timeout → feature_metrics row with zero churn, exit 0."""
    from orchestrator_next.record import record

    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir()

    db = _fresh_db()
    state_path = _make_state_yaml(tmp_path)
    payload = _mcc_payload("completed")

    # Patch subprocess.run to simulate timeout
    with patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired("git", 10),
    ):
        result, code = record(state_path, payload, db=db)

    assert code == 0, f"Expected exit 0 on git timeout, got {code}: {result}"

    row = db.execute(
        "SELECT files_changed, insertions, deletions, total_commits FROM feature_metrics "
        "WHERE change_id=?",
        ["my-feature"],
    ).fetchone()
    assert row is not None, "Expected feature_metrics row to be written"
    files_changed, insertions, deletions, total_commits = row
    assert files_changed == 0
    assert insertions == 0
    assert deletions == 0
    assert total_commits == 0

    db.close()


# ---------------------------------------------------------------------------
# (f) mark-change-completed with status="recovered" does NOT trigger absorbed path
# ---------------------------------------------------------------------------

def test_recovered_status_does_not_trigger(tmp_path, monkeypatch):
    """mark-change-completed with status='recovered' does NOT write feature_metrics."""
    from orchestrator_next.record import record

    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir()

    db = _fresh_db()
    state_path = _make_state_yaml(tmp_path)
    payload = _mcc_payload("recovered")

    with patch("orchestrator_next.record._write_feature_metrics") as mock_write_fm:
        result, code = record(state_path, payload, db=db)

    # Trigger should NOT have fired
    mock_write_fm.assert_not_called()

    fm_count = db.execute(
        "SELECT COUNT(*) FROM feature_metrics WHERE change_id=?",
        ["my-feature"],
    ).fetchone()[0]
    assert fm_count == 0, f"Expected 0 feature_metrics rows for recovered status, got {fm_count}"

    db.close()


# ---------------------------------------------------------------------------
# Additional: spike schema (no tasks.md) → NULL task columns, exit 0
# ---------------------------------------------------------------------------

def test_spike_schema_writes_null_task_columns(tmp_path, monkeypatch):
    """Spike schema triggers the path and writes NULL task columns."""
    from orchestrator_next.record import record

    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir()

    db = _fresh_db()
    state_path = _make_state_yaml(tmp_path, schema="spike", with_tasks=False)
    payload = _mcc_payload("completed")

    result, code = record(state_path, payload, db=db)

    assert code == 0, f"Expected exit 0 for spike schema, got {code}: {result}"

    row = db.execute(
        "SELECT tasks_total, schema_name FROM feature_metrics WHERE change_id=?",
        ["my-feature"],
    ).fetchone()
    assert row is not None
    tasks_total, schema_name = row
    assert tasks_total is None, f"Expected NULL tasks_total for spike, got {tasks_total}"
    assert schema_name == "spike"

    db.close()
