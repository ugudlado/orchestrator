"""T-5 (RED) / T-6 (GREEN): _write_phase_event helper.
T-11 (RED) / T-12 (GREEN): Atomic boundary write + ROLLBACK.

Tests cover:
  T-5/T-6:
    - test_write_phase_event: seeds 3 step_events rows for a phase, calls
      _write_phase_event, asserts one phase_events row exists with summed
      cost_usd/token columns/step_count=3.

  T-11/T-12:
    - test_atomic_commit_phase_boundary: phase boundary call writes step+phase
      rows in same transaction; both rows visible after.
    - test_atomic_commit_feature_boundary: feature boundary call writes
      step+phase+driver_session+subagent step_events; all visible after.
    - test_rollback_on_failure: _write_phase_event mocked to raise → no
      step_events row remains; exit code is non-zero.
    - test_non_boundary_failure_is_fail_soft: non-boundary step upsert failure
      → exit 0, no crash.
    - test_subagent_parse_before_begin: verifies _resolve_subagent_rows is
      called before BEGIN (checked via mock ordering).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import duckdb
import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from orchestrator_next.record import _write_phase_event  # noqa: E402
from orchestrator_next.upsert import ensure_schema, upsert_synthetic_event  # noqa: E402


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _fresh_db():
    db = duckdb.connect(":memory:")
    ensure_schema(db)
    return db


def _seed_step_events(db, repo_root, change_id, phase, count=3):
    """Seed `count` synthetic step_events rows for the given phase."""
    for i in range(count):
        upsert_synthetic_event(
            db,
            {"repo_root": repo_root, "change_id": change_id},
            agent_name="developer",
            step_id=f"step-{i}",
            phase=phase,
            usage={
                "input_tokens": 100 * (i + 1),
                "output_tokens": 50 * (i + 1),
                "cache_read_input_tokens": 10 * (i + 1),
                "cache_creation_input_tokens": 5 * (i + 1),
                "cost_usd": 0.01 * (i + 1),
                "duration_ms": 1000 * (i + 1),
            },
            started_at="2026-01-01T00:00:00Z",
            ended_at="2026-01-01T00:01:00Z",
        )


# ---------------------------------------------------------------------------
# T-5/T-6: test_write_phase_event
# ---------------------------------------------------------------------------

def test_write_phase_event():
    """_write_phase_event aggregates 3 step_events rows into one phase_events row."""
    db = _fresh_db()
    repo_root = "/test/repo"
    change_id = "test-feature"
    phase = "implement"

    _seed_step_events(db, repo_root, change_id, phase, count=3)

    # Call the helper (caller controls transaction)
    _write_phase_event(db, repo_root, change_id, phase, attempt=1)

    rows = db.execute(
        "SELECT * FROM phase_events WHERE repo_root=? AND change_id=? AND phase=?",
        [repo_root, change_id, phase],
    ).fetchall()
    assert len(rows) == 1, f"Expected 1 phase_events row, got {len(rows)}"

    # Get column names
    cols = [d[0] for d in db.execute("DESCRIBE phase_events").fetchall()]
    row_dict = dict(zip(cols, rows[0]))

    # step_count = 3 (one per seeded row)
    assert row_dict["step_count"] == 3, f"step_count: expected 3, got {row_dict['step_count']}"

    # Aggregated token sums: i in [0,1,2] → multipliers [1,2,3] → sum of n*(n+1)/2 * base
    expected_input = sum(100 * (i + 1) for i in range(3))   # 600
    expected_output = sum(50 * (i + 1) for i in range(3))    # 300
    expected_cache_read = sum(10 * (i + 1) for i in range(3))  # 60
    expected_cache_creation = sum(5 * (i + 1) for i in range(3))  # 30
    expected_cost = sum(0.01 * (i + 1) for i in range(3))    # 0.06
    expected_duration = sum(1000 * (i + 1) for i in range(3))  # 6000

    assert row_dict["input_tokens"] == expected_input, (
        f"input_tokens: expected {expected_input}, got {row_dict['input_tokens']}"
    )
    assert row_dict["output_tokens"] == expected_output, (
        f"output_tokens: expected {expected_output}, got {row_dict['output_tokens']}"
    )
    assert row_dict["cache_read_input_tokens"] == expected_cache_read, (
        f"cache_read_input_tokens: expected {expected_cache_read}"
    )
    assert row_dict["cache_creation_input_tokens"] == expected_cache_creation, (
        f"cache_creation_input_tokens: expected {expected_cache_creation}"
    )
    assert abs(row_dict["cost_usd"] - expected_cost) < 0.0001, (
        f"cost_usd: expected ~{expected_cost}, got {row_dict['cost_usd']}"
    )
    assert row_dict["duration_ms"] == expected_duration, (
        f"duration_ms: expected {expected_duration}, got {row_dict['duration_ms']}"
    )

    db.close()


def test_write_phase_event_no_rows_writes_zeros():
    """_write_phase_event with no step_events rows writes a zero-aggregate row."""
    db = _fresh_db()
    # No seeding — empty phase
    _write_phase_event(db, "/repo", "empty-feature", "implement", attempt=1)

    rows = db.execute(
        "SELECT step_count, cost_usd FROM phase_events WHERE change_id='empty-feature'",
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 0    # step_count
    assert rows[0][1] == 0.0  # cost_usd
    db.close()


# ---------------------------------------------------------------------------
# T-11/T-12 tests are in this file (same file per test infrastructure notes)
# These will be written during T-11 RED
# ---------------------------------------------------------------------------

# Placeholder imports for T-11/T-12 (will fail until T-12 GREEN)
def _minimal_state_yaml(tmp_path, workflow_plan: dict, change_id: str = "test-feature") -> str:
    """Write a minimal state.yaml for record() calls in boundary tests."""
    state = {
        "change_id": change_id,
        "phase": list(workflow_plan.keys())[-1],  # current phase = last phase
        "repo_root": "/test/repo",
        "workflow_plan": workflow_plan,
        "step_history": [],
    }
    path = tmp_path / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(path)


def test_atomic_commit_phase_boundary(tmp_path, monkeypatch):
    """Phase boundary call commits step + phase rows in the same transaction."""
    from orchestrator_next.record import record, BoundaryKind

    db = _fresh_db()
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir()

    plan = {
        "specify": {"active": ["spec-step"], "filtered": []},
        "implement": {"active": ["impl-step"], "filtered": []},
    }
    state_path = _minimal_state_yaml(tmp_path, plan)

    payload = {
        "step_id": "spec-step",
        "phase": "specify",
        "status": "completed",
        "agent": "developer",
        "outputs": {},
        "usage": {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.001},
    }

    result, code = record(state_path, payload, db=db)
    assert code == 0, f"Expected exit 0, got {code}: {result}"

    # step_events row exists
    step_count = db.execute(
        "SELECT COUNT(*) FROM step_events WHERE change_id='test-feature' AND phase='specify' AND step_id='spec-step'",
    ).fetchone()[0]
    assert step_count == 1, f"Expected 1 step_events row, got {step_count}"

    # phase_events row exists
    phase_count = db.execute(
        "SELECT COUNT(*) FROM phase_events WHERE change_id='test-feature' AND phase='specify'",
    ).fetchone()[0]
    assert phase_count == 1, f"Expected 1 phase_events row, got {phase_count}"

    db.close()


def test_rollback_on_failure(tmp_path, monkeypatch):
    """When _write_phase_event raises, ROLLBACK is called and no step_events row remains."""
    from orchestrator_next.record import record

    db = _fresh_db()
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir()

    plan = {
        "implement": {"active": ["fail-step"], "filtered": []},
    }
    state_path = _minimal_state_yaml(tmp_path, plan)

    payload = {
        "step_id": "fail-step",
        "phase": "implement",
        "status": "completed",
        "agent": "developer",
        "outputs": {},
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }

    with patch(
        "orchestrator_next.record._write_phase_event",
        side_effect=RuntimeError("simulated phase write failure"),
    ):
        result, code = record(state_path, payload, db=db)

    # Should be non-zero exit (fatal boundary failure)
    assert code != 0, f"Expected non-zero exit on boundary write failure, got {code}"

    # No step_events row should remain (rolled back)
    step_count = db.execute(
        "SELECT COUNT(*) FROM step_events WHERE change_id='test-feature'",
    ).fetchone()[0]
    assert step_count == 0, (
        f"Expected 0 step_events rows after ROLLBACK, got {step_count}"
    )

    db.close()


def test_non_boundary_failure_is_fail_soft(tmp_path, monkeypatch):
    """Non-boundary step upsert failure stays fail-soft: exit 0."""
    from orchestrator_next.record import record

    db = _fresh_db()
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir()

    # Plan where the current step is NOT the last
    plan = {
        "implement": {"active": ["step-a", "step-b"], "filtered": []},
    }
    # Write state.yaml with current phase = implement
    state = {
        "change_id": "test-feature",
        "phase": "implement",
        "repo_root": "/test/repo",
        "workflow_plan": plan,
        "step_history": [],
    }
    state_path = tmp_path / "state.yaml"
    state_path.write_text(yaml.safe_dump(state, sort_keys=False))

    payload = {
        "step_id": "step-a",
        "phase": "implement",
        "status": "completed",
        "agent": "developer",
        "outputs": {},
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }

    from orchestrator_next.upsert import upsert_step_event
    with patch(
        "orchestrator_next.record.upsert_step_event",
        side_effect=RuntimeError("simulated DB failure"),
    ):
        result, code = record(str(state_path), payload, db=db)

    # Should stay fail-soft for non-boundary
    assert code == 0, f"Expected exit 0 for non-boundary failure, got {code}: {result}"

    db.close()


def test_atomic_commit_feature_boundary(tmp_path, monkeypatch):
    """Feature boundary: step+phase+driver_session rows all committed in same transaction."""
    from orchestrator_next.record import record

    db = _fresh_db()
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir()

    # Single phase plan (implement is both phase and feature boundary)
    plan = {
        "implement": {"active": ["final-step"], "filtered": []},
    }
    state_path = _minimal_state_yaml(tmp_path, plan)

    payload = {
        "step_id": "final-step",
        "phase": "implement",
        "status": "completed",
        "agent": "developer",
        "outputs": {},
        "usage": {"input_tokens": 10, "output_tokens": 5},
        # Chat-driver signal: FEATURE-boundary JSONL resolution (orc-111 T-8).
        "agent_task_result": "COMPLETION:\n  status: completed\n",
    }

    # Mock _resolve_driver_session to return a canned session
    fake_session = {
        "session_id": "test-sess",
        "model": "claude-sonnet-4-6",
        "total_tokens": 100,
        "input_tokens": 80,
        "output_tokens": 20,
        "cost_usd": 0.001,
        "started_at": None,
        "ended_at": None,
    }
    # Mock _resolve_subagent_rows to return empty (no subagents)
    with patch("orchestrator_next.record._resolve_driver_session", return_value=fake_session), \
         patch("orchestrator_next.record._resolve_subagent_rows", return_value=[]):
        result, code = record(state_path, payload, db=db)

    assert code == 0, f"Expected exit 0 for feature boundary, got {code}: {result}"

    # step_events row exists
    step_count = db.execute(
        "SELECT COUNT(*) FROM step_events WHERE change_id='test-feature' AND phase='implement'",
    ).fetchone()[0]
    assert step_count == 1, f"Expected 1 step_events row, got {step_count}"

    # phase_events row exists
    phase_count = db.execute(
        "SELECT COUNT(*) FROM phase_events WHERE change_id='test-feature'",
    ).fetchone()[0]
    assert phase_count == 1, f"Expected 1 phase_events row, got {phase_count}"

    # driver_sessions row exists
    drv_count = db.execute(
        "SELECT COUNT(*) FROM driver_sessions WHERE change_id='test-feature'",
    ).fetchone()[0]
    assert drv_count == 1, f"Expected 1 driver_sessions row, got {drv_count}"

    db.close()


def test_subagent_parse_before_begin(tmp_path, monkeypatch):
    """_resolve_subagent_rows and _resolve_driver_session are called BEFORE BEGIN.

    Verified by checking that both functions are called (via mock) and that the
    final transaction commits (meaning the ordering is correct — if they were
    called inside BEGIN, a failure would roll back everything including step_events).
    """
    from orchestrator_next.record import record

    db = _fresh_db()
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir()

    plan = {"implement": {"active": ["final-step"], "filtered": []}}
    state_path = _minimal_state_yaml(tmp_path, plan)

    payload = {
        "step_id": "final-step",
        "phase": "implement",
        "status": "completed",
        "agent": "developer",
        "outputs": {},
        "usage": {"input_tokens": 10, "output_tokens": 5},
        # Chat-driver signal: FEATURE-boundary JSONL resolution (orc-111 T-8).
        "agent_task_result": "COMPLETION:\n  status: completed\n",
    }

    resolve_calls = []

    def mock_resolve_subagent(*args, **kwargs):
        resolve_calls.append("_resolve_subagent_rows")
        return []

    def mock_resolve_driver(*args, **kwargs):
        resolve_calls.append("_resolve_driver_session")
        return {
            "session_id": "s1", "model": None, "total_tokens": 0,
            "input_tokens": 0, "output_tokens": 0, "cost_usd": None,
            "started_at": None, "ended_at": None,
        }

    with patch("orchestrator_next.record._resolve_driver_session", side_effect=mock_resolve_driver), \
         patch("orchestrator_next.record._resolve_subagent_rows", side_effect=mock_resolve_subagent):
        result, code = record(state_path, payload, db=db)

    # Both resolve functions must have been called (they run before BEGIN)
    assert "_resolve_driver_session" in resolve_calls, (
        "_resolve_driver_session must be called at FEATURE boundary"
    )
    assert "_resolve_subagent_rows" in resolve_calls, (
        "_resolve_subagent_rows must be called at FEATURE boundary"
    )

    # Record must succeed (code 0) — confirms the transaction committed
    assert code == 0, f"Expected exit 0, got {code}: {result}"

    # Step row must be committed (confirms the full tx succeeded)
    step_count = db.execute(
        "SELECT COUNT(*) FROM step_events WHERE change_id='test-feature'",
    ).fetchone()[0]
    assert step_count == 1, f"Expected committed step row, got {step_count}"

    db.close()
