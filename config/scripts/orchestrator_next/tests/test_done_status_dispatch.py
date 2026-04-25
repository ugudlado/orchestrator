"""T-9 (RED) / T-10 (GREEN): record() status dispatch for completed/recovered/abandoned.

Tests cover FR-2 — completed/recovered/abandoned must each produce documented behavior.

Cases:
  (a) status: completed → step_events row written normally; exit 0
  (b) status: recovered → step_events row with status=recovered; no boundary check
      even on last-step payload; exit 0
  (c) status: abandoned → state.yaml.status set to 'blocked'; step_events row
      with status=abandoned; no boundary check; exit 0
  (d) missing status → defaults to 'completed' behavior
  (e) invalid status → exit 3
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import duckdb
import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from orchestrator_next.record import record  # noqa: E402
from orchestrator_next.upsert import ensure_schema  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_db():
    db = duckdb.connect(":memory:")
    ensure_schema(db)
    return db


def _write_state(tmp_path, *, change_id="test-feature", phase="implement",
                 workflow_plan=None) -> str:
    """Write a minimal state.yaml and return its path."""
    if workflow_plan is None:
        workflow_plan = {
            "specify": {"active": ["spec-step"], "filtered": []},
            "implement": {"active": ["step-a", "last-step"], "filtered": []},
        }
    state = {
        "change_id": change_id,
        "phase": phase,
        "repo_root": "/test/repo",
        "workflow_plan": workflow_plan,
        "step_history": [],
    }
    path = tmp_path / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(path)


def _isolate_contracts(tmp_path, monkeypatch):
    """Set an empty contracts dir so contract validation is skipped."""
    empty = tmp_path / "empty_contracts"
    empty.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(empty))


# ---------------------------------------------------------------------------
# (a) status: completed → step_events row written normally
# ---------------------------------------------------------------------------

def test_completed_writes_step_events_row(tmp_path, monkeypatch):
    """status: completed → step_events row exists, exit 0."""
    _isolate_contracts(tmp_path, monkeypatch)
    db = _fresh_db()
    state_path = _write_state(tmp_path)

    payload = {
        "step_id": "step-a",
        "phase": "implement",
        "status": "completed",
        "agent": "inline",
        "outputs": {},
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }

    result, code = record(state_path, payload, db=db)
    assert code == 0, f"Expected exit 0, got {code}: {result}"

    row_count = db.execute(
        "SELECT COUNT(*) FROM step_events WHERE change_id='test-feature' AND status='completed'",
    ).fetchone()[0]
    assert row_count == 1, f"Expected 1 completed step_events row, got {row_count}"

    db.close()


# ---------------------------------------------------------------------------
# (b) status: recovered → step row with status=recovered; no boundary check
# ---------------------------------------------------------------------------

def test_recovered_writes_step_with_recovered_status(tmp_path, monkeypatch):
    """status: recovered → step_events row has status=recovered; no phase_events row."""
    _isolate_contracts(tmp_path, monkeypatch)
    db = _fresh_db()

    # Use a plan where 'last-step' IS the phase boundary
    plan = {
        "implement": {"active": ["last-step"], "filtered": []},
    }
    state_path = _write_state(tmp_path, workflow_plan=plan)

    payload = {
        "step_id": "last-step",
        "phase": "implement",
        "status": "recovered",
        "agent": "inline",
        "outputs": {},
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }

    result, code = record(state_path, payload, db=db)
    assert code == 0, f"Expected exit 0 for recovered, got {code}: {result}"

    # step_events row with status=recovered
    step_row = db.execute(
        "SELECT status FROM step_events WHERE change_id='test-feature' AND step_id='last-step'",
    ).fetchone()
    assert step_row is not None, "Expected step_events row for recovered"
    assert step_row[0] == "recovered", f"Expected status=recovered, got {step_row[0]!r}"

    # No phase_events row (boundary check skipped for recovered)
    phase_count = db.execute(
        "SELECT COUNT(*) FROM phase_events WHERE change_id='test-feature'",
    ).fetchone()[0]
    assert phase_count == 0, (
        f"Expected 0 phase_events rows for recovered status, got {phase_count}"
    )

    db.close()


# ---------------------------------------------------------------------------
# (c) status: abandoned → state.yaml.status=blocked; step_events with abandoned
# ---------------------------------------------------------------------------

def test_abandoned_sets_state_blocked(tmp_path, monkeypatch):
    """status: abandoned → state.yaml.status set to 'blocked'."""
    _isolate_contracts(tmp_path, monkeypatch)
    db = _fresh_db()

    plan = {
        "implement": {"active": ["step-a", "last-step"], "filtered": []},
    }
    state_path = _write_state(tmp_path, workflow_plan=plan)

    payload = {
        "step_id": "step-a",
        "phase": "implement",
        "status": "abandoned",
        "agent": "inline",
        "outputs": {},
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }

    result, code = record(state_path, payload, db=db)
    assert code == 0, f"Expected exit 0 for abandoned, got {code}: {result}"

    # state.yaml.status should be 'blocked'
    state = yaml.safe_load(Path(state_path).read_text())
    assert state.get("status") == "blocked", (
        f"Expected state.status='blocked', got {state.get('status')!r}"
    )

    db.close()


def test_abandoned_writes_step_with_abandoned_status(tmp_path, monkeypatch):
    """status: abandoned → step_events row has status=abandoned."""
    _isolate_contracts(tmp_path, monkeypatch)
    db = _fresh_db()

    plan = {"implement": {"active": ["step-a"], "filtered": []}}
    state_path = _write_state(tmp_path, workflow_plan=plan)

    payload = {
        "step_id": "step-a",
        "phase": "implement",
        "status": "abandoned",
        "agent": "inline",
        "outputs": {},
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }

    result, code = record(state_path, payload, db=db)
    assert code == 0

    step_row = db.execute(
        "SELECT status FROM step_events WHERE change_id='test-feature' AND step_id='step-a'",
    ).fetchone()
    assert step_row is not None
    assert step_row[0] == "abandoned", f"Expected status=abandoned, got {step_row[0]!r}"

    db.close()


def test_abandoned_no_boundary_check_on_last_step(tmp_path, monkeypatch):
    """status: abandoned on phase-boundary step → no phase_events row."""
    _isolate_contracts(tmp_path, monkeypatch)
    db = _fresh_db()

    # 'last-step' IS the feature boundary
    plan = {"implement": {"active": ["last-step"], "filtered": []}}
    state_path = _write_state(tmp_path, workflow_plan=plan)

    payload = {
        "step_id": "last-step",
        "phase": "implement",
        "status": "abandoned",
        "agent": "inline",
        "outputs": {},
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }

    result, code = record(state_path, payload, db=db)
    assert code == 0

    phase_count = db.execute(
        "SELECT COUNT(*) FROM phase_events WHERE change_id='test-feature'",
    ).fetchone()[0]
    assert phase_count == 0, (
        f"Expected 0 phase_events rows for abandoned status (no boundary), got {phase_count}"
    )

    db.close()


# ---------------------------------------------------------------------------
# (d) missing status → defaults to 'completed' behavior
# ---------------------------------------------------------------------------

def test_missing_status_defaults_to_completed(tmp_path, monkeypatch):
    """Payload without 'status' key → treated as 'completed'."""
    _isolate_contracts(tmp_path, monkeypatch)
    db = _fresh_db()
    state_path = _write_state(tmp_path)

    payload = {
        "step_id": "step-a",
        "phase": "implement",
        # No 'status' key
        "agent": "inline",
        "outputs": {},
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }

    result, code = record(state_path, payload, db=db)
    assert code == 0, f"Expected exit 0 when status absent, got {code}: {result}"
    assert result.get("action") == "recorded"

    # Step row should have status='completed'
    step_row = db.execute(
        "SELECT status FROM step_events WHERE change_id='test-feature' AND step_id='step-a'",
    ).fetchone()
    assert step_row is not None
    assert step_row[0] == "completed"

    db.close()


# ---------------------------------------------------------------------------
# (e) invalid status → exit 3
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_status", ["in_progress", "blocked", "COMPLETED", "done", ""])
def test_invalid_status_returns_exit_3(tmp_path, monkeypatch, bad_status):
    """Unrecognized status value → exit 3 with clear error."""
    _isolate_contracts(tmp_path, monkeypatch)
    state_path = _write_state(tmp_path)

    payload = {
        "step_id": "step-a",
        "phase": "implement",
        "status": bad_status,
        "agent": "inline",
        "outputs": {},
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }

    result, code = record(state_path, payload)
    assert code == 3, f"Expected exit 3 for invalid status={bad_status!r}, got {code}: {result}"
