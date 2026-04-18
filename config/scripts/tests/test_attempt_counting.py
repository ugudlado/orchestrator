"""
Retry / crash-mid-step attempt counting tests (T-5 RED, T-6 GREEN).

These tests exercise _compute_attempt logic in dispatch.py:
  - crash-midstep fixture: prior attempts [1 completed, 2 failed, 2 in_progress
    (no ended_at)] → retry_step with attempt=3 (max scan, not just default +1).
  - after-retry-complete fixture: failed attempt:1 + completed attempt:2 for a
    step + another completed step → DuckDB has 3 rows; running CLI twice yields
    exactly the same 3 rows (idempotency).

Note: The _compute_attempt function and retry-detection branch were implemented
in T-2 (dispatch.py) as part of the broader dispatcher. These tests serve as a
regression guard covering the attempt-counting logic with more complex history
than the T-1 fixtures exercised (i.e., max-scan returning > 2, and the
idempotency of the upsert after a retry completes).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKTREE_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_FIXTURES_DIR = os.path.join(_HERE, "fixtures")
_STEP_CONTRACTS_DIR = os.path.join(_FIXTURES_DIR, "step_contracts")
_BIN_ORCHESTRATOR = os.path.join(_WORKTREE_ROOT, "bin", "orchestrator")
_SCRIPTS_DIR = os.path.join(_WORKTREE_ROOT, "config", "scripts")


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

    def test_crash_midstep_returns_retry_with_max_scan_attempt(self):
        """
        state-crash-midstep.yaml: prior entries have attempt=[1,2,2]; last is
        in_progress with no ended_at. _compute_attempt must return max(1,2,2)+1=3.

        This test exercises the max-scan branch of _compute_attempt — not just
        the default +1 path covered by state-in-progress-no-ended.yaml (which
        has only one prior in_progress entry with attempt=1, yielding attempt=2).
        """
        fixture = "state-crash-midstep.yaml"
        result = _run_next(fixture, self._metrics_db)

        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        actual = json.loads(result.stdout)

        self.assertEqual(actual.get("action"), "retry_step",
                         f"Expected action=retry_step, got: {actual.get('action')}")
        self.assertEqual(actual.get("attempt"), 3,
                         f"Expected attempt=3 (max scan of [1,2,2]+1), got: {actual.get('attempt')}")
        self.assertEqual(actual.get("previous_failure"), "no ended_at",
                         f"Expected previous_failure='no ended_at', got: {actual.get('previous_failure')}")
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
