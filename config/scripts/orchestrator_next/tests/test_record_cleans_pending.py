"""T-10/T-12: record() cleans up in_progress rows — DB + state.yaml.

Scenarios (T-10):
  (a) In_progress row gone from DB after terminal record().
  (b) In_progress entry gone from state.yaml step_history after record().
  (c) sum_cost_usd unaffected by NULL cost_usd in_progress rows.
  (d) Offline (db=None) path still scrubs state.yaml in_progress entry.

Scenario (T-12):
  Two-cycle next→record→next→record integration asserts zero lingering
  in_progress rows in the DB (AC-10).
"""
from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path

import duckdb
import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import orchestrator_next.record as _record_mod
from orchestrator_next.record import record  # noqa: E402
from orchestrator_next.upsert import ensure_schema, upsert_pending_step_event, sum_cost_usd  # noqa: E402


# ---------------------------------------------------------------------------
# Shared stub contract (no repeat_until; outputs required by validation)
# ---------------------------------------------------------------------------

_STUB_CONTRACT = textwrap.dedent("""\
    id: execute-next-task
    agent: developer
    instruction: Execute the next task.
    rules: []
    inputs: []
    outputs:
      - task_execution_result
""")

_STUB_CONTRACT_STEP2 = textwrap.dedent("""\
    id: review-task
    agent: developer
    instruction: Review the task.
    rules: []
    inputs: []
    outputs:
      - review_result
""")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = "/repo/root"
_CHANGE_ID = "test-feature"


def _write_state(tmp_path, *, step_history=None, step_ids=None) -> str:
    """Write a minimal state.yaml with optional pre-seeded step_history."""
    active = step_ids or ["execute-next-task"]
    state = {
        "change_id": _CHANGE_ID,
        "phase": "implement",
        "repo_root": _REPO_ROOT,
        "workflow_plan": {
            "implement": {
                "active": active,
                "filtered": [],
            }
        },
        "step_history": step_history or [],
        "worktree_path": str(tmp_path),
    }
    path = tmp_path / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(path)


def _in_progress_yaml_entry(step_id: str, phase: str = "implement", attempt: int = 1) -> dict:
    """Build a dict representing an in_progress state.yaml step_history entry."""
    return {
        "step_id": step_id,
        "phase": phase,
        "status": "in_progress",
        "agent": "developer",
        "attempt": attempt,
        "started_at": "2026-01-01T00:00:00Z",
        "ended_at": None,
        "usage": {},
        "evidence": {"outputs": {}},
    }


def _terminal_payload(step_id: str = "execute-next-task", phase: str = "implement") -> dict:
    """Build a completed step payload."""
    return {
        "step_id": step_id,
        "phase": phase,
        "status": "completed",
        "agent": "inline",
        "outputs": {"task_execution_result": {"task_id": "T-1"}},
        "usage": {},
    }


def _seed_db_in_progress(
    db,
    step_id: str = "execute-next-task",
    phase: str = "implement",
    attempt: int = 1,
) -> None:
    """Seed a single in_progress row in step_events via upsert_pending_step_event."""
    upsert_pending_step_event(
        db,
        repo_root=_REPO_ROOT,
        change_id=_CHANGE_ID,
        phase=phase,
        step_id=step_id,
        attempt=attempt,
        agent_name="developer",
        started_at="2026-01-01T00:00:00Z",
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_pricing_cache():
    """Prevent stale in-process pricing cache across tests."""
    _record_mod._pricing_cache.clear()
    yield
    _record_mod._pricing_cache.clear()


@pytest.fixture()
def in_memory_db():
    """In-memory DuckDB with full schema."""
    db = duckdb.connect(":memory:")
    ensure_schema(db)
    yield db
    db.close()


@pytest.fixture(autouse=True)
def setup_contracts(tmp_path, monkeypatch):
    """Isolate contracts dir and set env vars for each test."""
    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir()
    (contracts_dir / "execute-next-task.yaml").write_text(_STUB_CONTRACT)
    (contracts_dir / "review-task.yaml").write_text(_STUB_CONTRACT_STEP2)
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts_dir))
    _worktree_root = str(
        Path(_HERE).parent.parent.parent.parent
    )
    monkeypatch.setenv("ORCHESTRATOR_HOME", _worktree_root)
    fn = getattr(_record_mod, "_load_routes", None)
    if fn is not None and hasattr(fn, "cache_clear"):
        fn.cache_clear()


# ---------------------------------------------------------------------------
# T-10 scenarios
# ---------------------------------------------------------------------------

class TestRecordCleansPending:

    def test_a_in_progress_db_row_gone_after_terminal_record(self, tmp_path, in_memory_db):
        """(a) After terminal record(), the in_progress DB row must be deleted.

        Pre-seed: one in_progress DB row + state.yaml entry for (step_id=execute-next-task, phase=implement).
        Post-record: SELECT in_progress rows → 0; SELECT completed rows → 1.

        Note: upsert_step_event (called by bin/orchestrator after record) writes the
        completed row. We simulate that call here to verify the full invariant: no
        in_progress row, exactly one completed row.
        """
        from orchestrator_next.upsert import upsert_step_event
        from orchestrator_next.parser import StepHistoryEntry

        state_path = _write_state(
            tmp_path,
            step_history=[_in_progress_yaml_entry("execute-next-task")],
        )
        _seed_db_in_progress(in_memory_db)

        result, exit_code = record(state_path, _terminal_payload(), db=in_memory_db)
        assert exit_code == 0, f"record() failed: {result}"

        # Simulate what bin/orchestrator does post-record: upsert the terminal entry.
        with open(state_path) as f:
            written_state = yaml.safe_load(f)
        terminal_history = [
            h for h in (written_state.get("step_history") or [])
            if h.get("step_id") == "execute-next-task" and h.get("status") == "completed"
        ]
        assert terminal_history, "record() must have appended a completed entry to state.yaml"
        te = terminal_history[0]
        terminal_entry = StepHistoryEntry(
            step_id=te["step_id"],
            phase=te["phase"],
            status=te["status"],
            agent=te.get("agent", "inline"),
            attempt=te.get("attempt", 1),
            started_at=te.get("started_at"),
            ended_at=te.get("ended_at"),
            usage=te.get("usage") or {},
            escalation=None,
            raw=te,
        )
        context = {"repo_root": _REPO_ROOT, "change_id": _CHANGE_ID}
        upsert_step_event(in_memory_db, terminal_entry, context)

        in_progress_count = in_memory_db.execute(
            "SELECT COUNT(*) FROM step_events WHERE repo_root=? AND change_id=? AND step_id=? AND status='in_progress'",
            [_REPO_ROOT, _CHANGE_ID, "execute-next-task"],
        ).fetchone()[0]
        assert in_progress_count == 0, (
            f"Expected 0 in_progress rows after terminal record, got {in_progress_count}"
        )

        completed_count = in_memory_db.execute(
            "SELECT COUNT(*) FROM step_events WHERE repo_root=? AND change_id=? AND step_id=? AND status='completed'",
            [_REPO_ROOT, _CHANGE_ID, "execute-next-task"],
        ).fetchone()[0]
        assert completed_count == 1, (
            f"Expected 1 completed row after terminal record, got {completed_count}"
        )

    def test_b_in_progress_state_yaml_entry_gone_after_terminal_record(self, tmp_path, in_memory_db):
        """(b) After terminal record(), the in_progress state.yaml entry must be stripped.

        Post-record: step_history has exactly one entry for (step_id, phase, attempt=1)
        with status=completed. No lingering in_progress entry.
        """
        state_path = _write_state(
            tmp_path,
            step_history=[_in_progress_yaml_entry("execute-next-task")],
        )
        _seed_db_in_progress(in_memory_db)

        result, exit_code = record(state_path, _terminal_payload(), db=in_memory_db)
        assert exit_code == 0, f"record() failed: {result}"

        with open(state_path) as f:
            state = yaml.safe_load(f)

        history = state.get("step_history") or []
        matching = [
            h for h in history
            if h.get("step_id") == "execute-next-task" and h.get("phase") == "implement"
        ]
        assert len(matching) == 1, (
            f"Expected exactly 1 history entry for (execute-next-task, implement), got {len(matching)}: {matching}"
        )
        assert matching[0]["status"] == "completed", (
            f"Expected status=completed, got {matching[0]['status']!r}"
        )
        assert matching[0]["attempt"] == 1, (
            f"Expected attempt=1 (in_progress excluded from prior_attempts calc), got {matching[0]['attempt']}"
        )

        in_progress_entries = [h for h in history if h.get("status") == "in_progress"]
        assert len(in_progress_entries) == 0, (
            f"Expected no in_progress entries after record, found: {in_progress_entries}"
        )

    def test_c_sum_cost_usd_unaffected_by_null_cost_in_progress_rows(self, tmp_path, in_memory_db):
        """(c) sum_cost_usd must skip NULL cost_usd on in_progress rows (COALESCE/SUM NULL-skip).

        Seed: one completed row with cost_usd=0.5, one in_progress row with NULL cost_usd.
        Assert: sum_cost_usd() returns 0.5 (not None, not 0.0, not error).
        """
        from orchestrator_next.upsert import upsert_step_event
        from orchestrator_next.parser import StepHistoryEntry

        completed_entry = StepHistoryEntry(
            step_id="execute-next-task",
            phase="implement",
            status="completed",
            agent="developer",
            attempt=1,
            started_at="2026-01-01T00:00:00Z",
            ended_at="2026-01-01T00:01:00Z",
            usage={"cost_usd": 0.5, "input_tokens": 1000, "output_tokens": 500},
            escalation=None,
            raw={},
        )
        context = {"repo_root": _REPO_ROOT, "change_id": _CHANGE_ID}
        upsert_step_event(in_memory_db, completed_entry, context)

        # Seed in_progress row (cost_usd is NULL by design)
        _seed_db_in_progress(in_memory_db, step_id="review-task", attempt=1)

        result = sum_cost_usd(in_memory_db, context)
        assert result == pytest.approx(0.5, rel=1e-9), (
            f"Expected sum_cost_usd=0.5 (NULL in_progress row skipped), got {result!r}"
        )

    def test_d_offline_db_none_still_scrubs_state_yaml(self, tmp_path):
        """(d) db=None offline path: in_progress state.yaml entry must still be stripped.

        No DB interaction. After record(db=None), state.yaml in_progress entry is removed
        and the terminal entry is appended.
        """
        state_path = _write_state(
            tmp_path,
            step_history=[_in_progress_yaml_entry("execute-next-task")],
        )

        result, exit_code = record(state_path, _terminal_payload(), db=None)
        assert exit_code == 0, f"record() failed with db=None: {result}"

        with open(state_path) as f:
            state = yaml.safe_load(f)

        history = state.get("step_history") or []
        in_progress_entries = [h for h in history if h.get("status") == "in_progress"]
        assert len(in_progress_entries) == 0, (
            f"Expected no in_progress entries (db=None path), found: {in_progress_entries}"
        )

        completed_entries = [
            h for h in history
            if h.get("step_id") == "execute-next-task" and h.get("status") == "completed"
        ]
        assert len(completed_entries) == 1, (
            f"Expected 1 completed entry appended (db=None path), got {len(completed_entries)}"
        )


# ---------------------------------------------------------------------------
# T-12: Two-cycle invariant
# ---------------------------------------------------------------------------

class TestTwoCycleInvariant:

    def test_two_cycle_no_lingering_in_progress_rows(self, tmp_path, in_memory_db):
        """T-12 (AC-10): After next→record→next→record for two steps, zero in_progress DB rows.

        Simulates:
          Cycle 1: upsert_pending(step=execute-next-task) + state.yaml in_progress entry
                   → record(execute-next-task)
          Cycle 2: upsert_pending(step=review-task) + state.yaml in_progress entry
                   → record(review-task)

        Final assert: SELECT COUNT(*) FROM step_events WHERE status='in_progress' = 0.
        """
        context = {"repo_root": _REPO_ROOT, "change_id": _CHANGE_ID}

        # ----- Cycle 1: "next" simulation -----
        state_path = _write_state(
            tmp_path,
            step_ids=["execute-next-task", "review-task"],
            step_history=[_in_progress_yaml_entry("execute-next-task")],
        )
        _seed_db_in_progress(in_memory_db, step_id="execute-next-task", attempt=1)

        # ----- Cycle 1: "record" -----
        payload1 = _terminal_payload(step_id="execute-next-task")
        result1, code1 = record(state_path, payload1, db=in_memory_db)
        assert code1 == 0, f"Cycle 1 record() failed: {result1}"

        # ----- Cycle 2: "next" simulation -----
        # Reload state and inject in_progress for review-task
        with open(state_path) as f:
            current_state = yaml.safe_load(f)
        current_history = current_state.get("step_history") or []
        current_history.append(_in_progress_yaml_entry("review-task"))
        current_state["step_history"] = current_history
        with open(state_path, "w") as f:
            yaml.safe_dump(current_state, f, sort_keys=False, default_flow_style=False)
        _seed_db_in_progress(in_memory_db, step_id="review-task", attempt=1)

        # ----- Cycle 2: "record" -----
        payload2 = {
            "step_id": "review-task",
            "phase": "implement",
            "status": "completed",
            "agent": "inline",
            "outputs": {"review_result": {"ok": True}},
            "usage": {},
        }
        result2, code2 = record(state_path, payload2, db=in_memory_db)
        assert code2 == 0, f"Cycle 2 record() failed: {result2}"

        # ----- Invariant: zero in_progress rows -----
        in_progress_count = in_memory_db.execute(
            "SELECT COUNT(*) FROM step_events WHERE status='in_progress'",
        ).fetchone()[0]
        assert in_progress_count == 0, (
            f"AC-10 violated: {in_progress_count} in_progress rows remain after two complete cycles"
        )
