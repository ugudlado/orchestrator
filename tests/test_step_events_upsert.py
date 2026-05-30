"""
DuckDB step_events upsert idempotency tests (T-3 RED, T-4 GREEN).

Each test creates a tempfile DuckDB database, calls the upsert module
directly (not via subprocess), and asserts correctness.

Test cases:
  1. basic_upsert — 2 terminal entries upserted; correct row count.
  2. idempotency — calling upsert twice yields same row count.
  3. otel_column_mapping — short names are mapped to OTel column names.
  4. inline_agent_null_tokens — script step with no agent produces row with
     agent_name=NULL and NULL token columns.
  5. slug_guard_rejects_bad_change_id — invalid change_id raises ValueError.
  6. escalation_pk_two_rows — two entries at same (phase, step_id, attempt) with
     different status produce TWO rows (PK includes status).
  7. dimension_keys_non_null — every row has non-null repo_root, change_id,
     phase, step_id, attempt.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conftest import ORCHESTRATOR_ROOT

# Ensure orchestrator_next is importable
_SCRIPTS_DIR = os.path.join(ORCHESTRATOR_ROOT, "config", "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

_FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
_STEP_CONTRACTS_DIR = os.path.join(_FIXTURES_DIR, "step_contracts")

# Set test env so parser can find step contracts
os.environ.setdefault("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", _STEP_CONTRACTS_DIR)


def _make_db(path: str):
    """Open a fresh DuckDB database at path."""
    import duckdb
    return duckdb.connect(path)


def _load_fixture(name: str) -> "State":  # noqa: F821
    from orchestrator_next.parser import load_state
    return load_state(os.path.join(_FIXTURES_DIR, name))


def _upsert_all(db, state, state_yaml_path: str) -> None:
    """Upsert all terminal step_history entries for state into db."""
    from orchestrator_next.upsert import ensure_schema, upsert_step_event
    from orchestrator_next.parser import load_state

    ensure_schema(db)
    context = {
        "repo_root": state.repo_root or "/test/repo",
        "change_id": state.change_id,
    }
    terminal_statuses = {"completed", "failed", "blocked", "escalate_to_architect"}
    for entry in state.step_history:
        if entry.status in terminal_statuses:
            upsert_step_event(db, entry, context)


class TestStepEventsUpsert(unittest.TestCase):
    """DuckDB step_events upsert correctness tests."""

    def setUp(self):
        """Create a fresh temp DuckDB file per test."""
        # Use a temp directory and construct a path — DuckDB creates the file
        # itself; passing an existing empty file causes an IO Error.
        self._tmpdir = tempfile.mkdtemp()
        self._db_path = os.path.join(self._tmpdir, "test.duckdb")
        self._db = _make_db(self._db_path)

    def tearDown(self):
        self._db.close()
        import shutil
        try:
            shutil.rmtree(self._tmpdir)
        except OSError:
            pass

    def test_basic_upsert_row_count(self):
        """2 terminal entries upserted → exactly 2 rows in step_events."""
        state = _load_fixture("state-upsert-basic.yaml")
        _upsert_all(self._db, state, os.path.join(_FIXTURES_DIR, "state-upsert-basic.yaml"))
        row = self._db.execute("SELECT COUNT(*) FROM step_events").fetchone()
        self.assertEqual(row[0], 2, "Expected 2 rows for 2 terminal entries")

    def test_idempotency(self):
        """Calling upsert twice on the same fixture yields the same row count."""
        state = _load_fixture("state-upsert-basic.yaml")
        yaml_path = os.path.join(_FIXTURES_DIR, "state-upsert-basic.yaml")
        _upsert_all(self._db, state, yaml_path)
        _upsert_all(self._db, state, yaml_path)  # second call — must be idempotent
        row = self._db.execute("SELECT COUNT(*) FROM step_events").fetchone()
        self.assertEqual(row[0], 2, "Idempotency failure: row count changed on second upsert")

    def test_otel_column_mapping(self):
        """Usage short names (input_tokens, cost_usd, model) are stored in step_events columns."""
        state = _load_fixture("state-upsert-basic.yaml")
        _upsert_all(self._db, state, os.path.join(_FIXTURES_DIR, "state-upsert-basic.yaml"))
        row = self._db.execute(
            """
            SELECT input_tokens,
                   output_tokens,
                   cache_read_input_tokens,
                   cost_usd,
                   model
            FROM step_events
            WHERE step_id = 'step-with-run'
            """
        ).fetchone()
        self.assertIsNotNone(row, "No row for step-with-run")
        self.assertEqual(row[0], 12000, "input_tokens mismatch")
        self.assertEqual(row[1], 1800, "output_tokens mismatch")
        self.assertEqual(row[2], 8500, "cache_read_input_tokens mismatch")
        self.assertAlmostEqual(row[3], 0.47, places=5, msg="cost_usd mismatch")
        self.assertEqual(row[4], "claude-sonnet-4-5", "model mismatch")

    def test_inline_agent_null_tokens(self):
        """Script step with no agent produces row with agent_name=NULL and NULL token columns."""
        state = _load_fixture("state-upsert-basic.yaml")
        _upsert_all(self._db, state, os.path.join(_FIXTURES_DIR, "state-upsert-basic.yaml"))
        row = self._db.execute(
            """
            SELECT agent_name,
                   input_tokens,
                   output_tokens,
                   cost_usd
            FROM step_events
            WHERE step_id = 'step-inline-only'
            """
        ).fetchone()
        self.assertIsNotNone(row, "No row for step-inline-only")
        self.assertEqual(row[0], "none", "agent_name must be 'none' sentinel for script steps with no agent")
        self.assertIsNone(row[1], "input_tokens should be NULL for script steps with no agent")
        self.assertIsNone(row[2], "output_tokens should be NULL for script steps with no agent")
        self.assertIsNone(row[3], "cost_usd should be NULL for script steps with no agent")

    def test_slug_guard_rejects_bad_change_id(self):
        """change_id failing slug guard raises ValueError before any DB write."""
        from orchestrator_next.upsert import ensure_schema, upsert_step_event
        from orchestrator_next.parser import StepHistoryEntry

        ensure_schema(self._db)
        entry = StepHistoryEntry(
            step_id="some-step",
            phase="implement",
            status="completed",
            agent=None,
            attempt=1,
            started_at="2026-04-18T10:00:00Z",
            ended_at="2026-04-18T10:30:00Z",
            usage={},
            escalation=None,
            raw={},
        )
        bad_context = {
            "repo_root": "/test/repo",
            "change_id": "Bad ID with spaces!",  # violates slug guard
        }
        with self.assertRaises(ValueError, msg="Should raise ValueError for bad change_id"):
            upsert_step_event(self._db, entry, bad_context)

        # Verify no row was written
        row = self._db.execute("SELECT COUNT(*) FROM step_events").fetchone()
        self.assertEqual(row[0], 0, "No row should be written when slug guard fails")

    def test_escalation_pk_two_rows(self):
        """
        Two entries at same (phase, step_id, attempt) with different status
        must produce TWO rows in step_events (PK includes status).

        This is the critical escalation audit trail test — confirms that
        status is part of the composite primary key.
        """
        state = _load_fixture("state-upsert-escalation.yaml")
        _upsert_all(self._db, state, os.path.join(_FIXTURES_DIR, "state-upsert-escalation.yaml"))

        total = self._db.execute(
            """
            SELECT COUNT(*) FROM step_events
            WHERE phase = 'implement'
              AND step_id = 'step-inline-only'
              AND attempt = 1
            """
        ).fetchone()[0]
        self.assertEqual(total, 2, (
            "Expected 2 rows for the same (phase, step_id, attempt) with different status. "
            "This confirms status is part of the composite PK (escalation audit trail)."
        ))

        statuses = set(
            row[0]
            for row in self._db.execute(
                """
                SELECT status FROM step_events
                WHERE phase = 'implement'
                  AND step_id = 'step-inline-only'
                  AND attempt = 1
                ORDER BY status
                """
            ).fetchall()
        )
        self.assertIn("escalate_to_architect", statuses)
        self.assertIn("completed", statuses)

    def test_dimension_keys_non_null(self):
        """Every row has non-null repo_root, change_id, phase, step_id, attempt."""
        state = _load_fixture("state-upsert-basic.yaml")
        _upsert_all(self._db, state, os.path.join(_FIXTURES_DIR, "state-upsert-basic.yaml"))
        rows = self._db.execute(
            """
            SELECT repo_root, change_id, phase, step_id, attempt
            FROM step_events
            """
        ).fetchall()
        self.assertGreater(len(rows), 0, "No rows found")
        for i, row in enumerate(rows):
            for j, col in enumerate(["repo_root", "change_id", "phase", "step_id", "attempt"]):
                self.assertIsNotNone(
                    row[j],
                    f"Row {i} column '{col}' is NULL — must be non-null per AC-4",
                )


if __name__ == "__main__":
    unittest.main()
