"""
Resume / crash-mid-step attempt counting tests (T-5 RED, T-6 GREEN).

These tests exercise resume semantics in dispatch.py:
  - crash-midstep fixture: prior attempts [1 completed, 2 failed, 2 in_progress
    (no ended_at)] → resume_step with attempt=2 (preserved from in_progress entry,
    NOT bumped via _compute_attempt). Under Phase 2, resume keeps the original
    attempt unchanged.
  - after-retry-complete fixture: failed attempt:1 + completed attempt:2 for a
    step + another completed step → DuckDB has 3 rows; running CLI twice yields
    exactly the same 3 rows (idempotency).

Note: The resume_step branch was implemented in T-6 (dispatch.py) replacing the
old retry_step branch. These tests serve as a regression guard covering the
attempt-preservation logic with complex history (in_progress entry with attempt=2
returns attempt=2, not max+1=3).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conftest import ORCHESTRATOR_ROOT

_FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
_STEP_CONTRACTS_DIR = os.path.join(_FIXTURES_DIR, "step_contracts")
_BIN_ORCHESTRATOR = os.path.join(ORCHESTRATOR_ROOT, "bin", "orchestrator")
_SCRIPTS_DIR = os.path.join(ORCHESTRATOR_ROOT, "config", "scripts")


def _run_next(fixture_name: str, metrics_db_path: str) -> subprocess.CompletedProcess:
    """Run `bin/orchestrator next <fixture>` and capture result."""
    fixture_path = os.path.join(_FIXTURES_DIR, fixture_name)
    env = os.environ.copy()
    env["METRICS_DB"] = metrics_db_path
    env["ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE"] = _STEP_CONTRACTS_DIR
    env["PYTHONPATH"] = _SCRIPTS_DIR
    env.pop("ORCHESTRATOR_HOME", None)
    return subprocess.run(
        [sys.executable, _BIN_ORCHESTRATOR, "next", fixture_path],
        capture_output=True,
        text=True,
        env=env,
    )


class TestAttemptCounting(unittest.TestCase):
    """Retry and crash-mid-step attempt counting tests."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="orch_attempt_test_")
        self._metrics_db = os.path.join(self._tmpdir, "test.duckdb")

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_crash_midstep_returns_resume_with_preserved_attempt(self):
        """
        state-crash-midstep.yaml: prior entries have attempt=[1,2,2]; last is
        in_progress with attempt=2 and no ended_at. Under Phase 2 resume semantics,
        dispatch preserves attempt=2 from the in_progress entry (does NOT call
        _compute_attempt which would return max(1,2,2)+1=3).

        This test verifies that resume_step preserves the original attempt number
        rather than bumping it — the key semantic difference from the retired
        retry_step branch.
        """
        fixture = "state-crash-midstep.yaml"
        result = _run_next(fixture, self._metrics_db)

        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        actual = json.loads(result.stdout)

        # Phase 2: resume_step preserves attempt=2 from the in_progress entry.
        self.assertEqual(actual.get("action"), "resume_step",
                         f"Expected action=resume_step, got: {actual.get('action')}")
        self.assertEqual(actual.get("attempt"), 2,
                         f"Expected attempt=2 (preserved from in_progress entry), got: {actual.get('attempt')}")
        self.assertTrue(actual.get("is_resume"),
                        f"Expected is_resume=True, got: {actual.get('is_resume')}")
        self.assertEqual(actual.get("step_id"), "step-inline-only",
                         f"Expected step_id='step-inline-only', got: {actual.get('step_id')}")

    def test_crash_midstep_mtime_unchanged(self):
        """state-crash-midstep.yaml: CLI must not mutate the fixture (pure-read)."""
        fixture = "state-crash-midstep.yaml"
        fixture_path = os.path.join(_FIXTURES_DIR, fixture)
        mtime_before = os.path.getmtime(fixture_path)
        _run_next(fixture, self._metrics_db)
        mtime_after = os.path.getmtime(fixture_path)
        self.assertEqual(mtime_before, mtime_after,
                         "state.yaml mtime changed — CLI must be pure-read")

    def test_after_retry_complete_idempotent_row_count(self):
        """
        state-after-retry-complete.yaml: attempt:1 failed + attempt:2 completed for
        step-inline-only, plus attempt:1 completed for step-with-run.

        Running CLI twice must produce exactly 3 rows in step_events (3 terminal
        entries). The upsert is idempotent — second run does not add rows.
        """
        import sys as _sys
        sys_path_backup = _sys.path[:]
        if _SCRIPTS_DIR not in _sys.path:
            _sys.path.insert(0, _SCRIPTS_DIR)

        try:
            import duckdb
            from orchestrator_next.upsert import ensure_schema, upsert_step_event
            from orchestrator_next.parser import load_state

            state_path = os.path.join(_FIXTURES_DIR, "state-after-retry-complete.yaml")
            state = load_state(state_path)
            context = {
                "repo_root": "/test/repo",
                "change_id": state.change_id,
            }
            terminal_statuses = {"completed", "failed", "blocked", "escalate_to_architect"}

            db = duckdb.connect(self._metrics_db)
            ensure_schema(db)

            # First upsert pass
            for entry in state.step_history:
                if entry.status in terminal_statuses:
                    upsert_step_event(db, entry, context)

            count1 = db.execute("SELECT COUNT(*) FROM step_events").fetchone()[0]
            self.assertEqual(count1, 3,
                             f"Expected 3 rows after first upsert (3 terminal entries), got {count1}")

            # Second upsert pass — must be idempotent
            for entry in state.step_history:
                if entry.status in terminal_statuses:
                    upsert_step_event(db, entry, context)

            count2 = db.execute("SELECT COUNT(*) FROM step_events").fetchone()[0]
            self.assertEqual(count2, 3,
                             f"Idempotency failure: row count changed to {count2} on second upsert")

            db.close()
        finally:
            _sys.path[:] = sys_path_backup

    def test_after_retry_complete_row_distinction(self):
        """
        state-after-retry-complete.yaml: the failed attempt:1 and completed
        attempt:2 entries for step-inline-only must each produce exactly one row.
        Both rows exist in step_events and are distinct by attempt number.
        """
        import sys as _sys
        sys_path_backup = _sys.path[:]
        if _SCRIPTS_DIR not in _sys.path:
            _sys.path.insert(0, _SCRIPTS_DIR)

        try:
            import duckdb
            from orchestrator_next.upsert import ensure_schema, upsert_step_event
            from orchestrator_next.parser import load_state

            state_path = os.path.join(_FIXTURES_DIR, "state-after-retry-complete.yaml")
            state = load_state(state_path)
            context = {
                "repo_root": "/test/repo",
                "change_id": state.change_id,
            }
            terminal_statuses = {"completed", "failed", "blocked", "escalate_to_architect"}

            db = duckdb.connect(self._metrics_db)
            ensure_schema(db)
            for entry in state.step_history:
                if entry.status in terminal_statuses:
                    upsert_step_event(db, entry, context)

            # Both attempt:1 and attempt:2 rows for step-inline-only must exist
            rows = db.execute(
                """
                SELECT attempt, status
                FROM step_events
                WHERE step_id = 'step-inline-only'
                ORDER BY attempt
                """
            ).fetchall()
            self.assertEqual(len(rows), 2,
                             f"Expected 2 rows for step-inline-only (attempt:1 failed + attempt:2 completed), got {len(rows)}")
            attempt_statuses = {row[0]: row[1] for row in rows}
            self.assertEqual(attempt_statuses.get(1), "failed",
                             f"Attempt 1 should have status=failed, got: {attempt_statuses.get(1)}")
            self.assertEqual(attempt_statuses.get(2), "completed",
                             f"Attempt 2 should have status=completed, got: {attempt_statuses.get(2)}")

            db.close()
        finally:
            _sys.path[:] = sys_path_backup


if __name__ == "__main__":
    unittest.main()
