"""
Regression test for ORC-44: CONVENTIONS.md used 'tools:' key in usage blocks
but upsert.py reads 'tool_calls:'. This test verifies the correct key produces
non-zero DuckDB rows, and the wrong key produces zero rows.
"""
from __future__ import annotations

import os
import sys
import shutil
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conftest import ORCHESTRATOR_ROOT

_SCRIPTS_DIR = os.path.join(ORCHESTRATOR_ROOT, "config", "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

_FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
_STEP_CONTRACTS_DIR = os.path.join(_FIXTURES_DIR, "step_contracts")
os.environ.setdefault("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", _STEP_CONTRACTS_DIR)


def _make_entry(usage: dict):
    from orchestrator_next.parser import StepHistoryEntry
    return StepHistoryEntry(
        step_id="execute-next-task",
        phase="main",
        status="completed",
        agent="developer",
        attempt=1,
        started_at="2026-05-09T10:00:00Z",
        ended_at="2026-05-09T10:30:00Z",
        usage=usage,
        escalation=None,
        raw={},
    )


def _setup_db(path: str):
    import duckdb
    from orchestrator_next.upsert import ensure_schema
    db = duckdb.connect(path)
    ensure_schema(db)
    return db


def _upsert(db, entry):
    from orchestrator_next.upsert import upsert_step_event
    upsert_step_event(db, entry, {"repo_root": "/test/repo", "change_id": "orc-44-test"})


def _count_rows(db) -> int:
    return db.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0]


class TestToolCallsKeyConventions(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db_path = os.path.join(self._tmpdir, "test.duckdb")
        self._db = _setup_db(self._db_path)

    def tearDown(self):
        self._db.close()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_tool_calls_key_produces_rows(self):
        """usage with 'tool_calls:' key (correct per upsert.py) fans out to DuckDB rows."""
        entry = _make_entry({"tool_calls": {"Read": 3, "Bash": 2, "Edit": 1, "Grep": 1}})
        _upsert(self._db, entry)
        self.assertEqual(_count_rows(self._db), 7)

    def test_wrong_tools_key_produces_zero_rows(self):
        """usage with 'tools:' key (wrong — old CONVENTIONS.md doc) fans out zero rows."""
        entry = _make_entry({"tools": {"Read": 3, "Bash": 2, "Edit": 1, "Grep": 1}})
        _upsert(self._db, entry)
        self.assertEqual(_count_rows(self._db), 0)

    def test_conventions_example_exact_shape(self):
        """The exact usage block from CONVENTIONS.md step_history example produces rows."""
        # Mirrors the example at CONVENTIONS.md § Standard step_history entry
        usage = {
            "input_tokens": 12000,
            "output_tokens": 3500,
            "cache_creation_input_tokens": 2800,
            "cache_read_input_tokens": 200,
            "total_tokens": 18500,
            "cost_usd": 0.0023,
            "tool_uses": 7,
            "tool_calls": {
                "Read": 3,
                "Bash": 2,
                "Edit": 1,
                "Grep": 1,
            },
            "duration_ms": 42000,
        }
        entry = _make_entry(usage)
        _upsert(self._db, entry)
        self.assertEqual(_count_rows(self._db), 7)


if __name__ == "__main__":
    unittest.main()
