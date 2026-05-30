"""
tool_calls upsert tests (T-1).

Tests for `upsert_step_event()` fanning out `usage.tool_calls` into
per-call rows in the `tool_calls` table.

Cases:
  (a) empty/missing usage.tool_calls → zero rows
  (b) pure native {"Bash": 3} → 3 rows all is_mcp=false with call_seq 1,2,3
  (c) pure MCP {"mcp__pal__thinkdeep": 1} → 1 row is_mcp=true
  (d) mixed → rows sum correctly, call_seq monotonic, agent_name denormalized
  (e) idempotency: retry with fewer tools doesn't leave orphan rows
  (f) AC-1 acceptance criterion: Bash:2, mcp__pal__thinkdeep:1 → 3 rows, correct is_mcp flags
"""
from __future__ import annotations

import os
import sys
import tempfile
import shutil
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conftest import ORCHESTRATOR_ROOT

_SCRIPTS_DIR = os.path.join(ORCHESTRATOR_ROOT, "config", "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

_FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
_STEP_CONTRACTS_DIR = os.path.join(_FIXTURES_DIR, "step_contracts")
os.environ.setdefault("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", _STEP_CONTRACTS_DIR)


def _make_db(path: str):
    import duckdb
    return duckdb.connect(path)


def _make_entry(
    step_id: str = "step-test",
    phase: str = "implement",
    status: str = "completed",
    agent: str = "developer",
    attempt: int = 1,
    tool_calls: dict | None = None,
):
    from orchestrator_next.parser import StepHistoryEntry
    usage = {}
    if tool_calls is not None:
        usage["tool_calls"] = tool_calls
    return StepHistoryEntry(
        step_id=step_id,
        phase=phase,
        status=status,
        agent=agent,
        attempt=attempt,
        started_at="2026-04-18T10:00:00Z",
        ended_at="2026-04-18T10:30:00Z",
        usage=usage,
        escalation=None,
        raw={},
    )


def _upsert(db, entry, change_id: str = "test-change", repo_root: str = "/test/repo"):
    from orchestrator_next.upsert import upsert_step_event
    upsert_step_event(db, entry, {"repo_root": repo_root, "change_id": change_id})


def _count_rows(db) -> int:
    return db.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0]


class TestToolCallsUpsert(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db_path = os.path.join(self._tmpdir, "test.duckdb")
        import duckdb
        self._db = duckdb.connect(self._db_path)
        from orchestrator_next.upsert import ensure_schema
        ensure_schema(self._db)

    def tearDown(self):
        self._db.close()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_empty_tool_calls_produces_zero_rows(self):
        """Empty usage.tool_calls dict → zero rows in tool_calls."""
        entry = _make_entry(tool_calls={})
        _upsert(self._db, entry)
        self.assertEqual(_count_rows(self._db), 0)

    def test_missing_tool_calls_produces_zero_rows(self):
        """No usage.tool_calls key → zero rows in tool_calls."""
        entry = _make_entry(tool_calls=None)
        _upsert(self._db, entry)
        self.assertEqual(_count_rows(self._db), 0)

    def test_pure_native_three_calls(self):
        """{"Bash": 3} → 3 rows, all is_mcp=false, call_seq 1,2,3."""
        entry = _make_entry(tool_calls={"Bash": 3})
        _upsert(self._db, entry)
        self.assertEqual(_count_rows(self._db), 3)

        rows = self._db.execute(
            "SELECT tool_name, is_mcp, call_seq FROM tool_calls ORDER BY call_seq"
        ).fetchall()
        self.assertEqual(len(rows), 3)
        for i, (tool_name, is_mcp, call_seq) in enumerate(rows, start=1):
            self.assertEqual(tool_name, "Bash")
            self.assertFalse(is_mcp)
            self.assertEqual(call_seq, i)

    def test_pure_mcp_one_call(self):
        """{"mcp__pal__thinkdeep": 1} → 1 row, is_mcp=true."""
        entry = _make_entry(tool_calls={"mcp__pal__thinkdeep": 1})
        _upsert(self._db, entry)
        self.assertEqual(_count_rows(self._db), 1)

        row = self._db.execute(
            "SELECT tool_name, is_mcp, call_seq FROM tool_calls"
        ).fetchone()
        self.assertEqual(row[0], "mcp__pal__thinkdeep")
        self.assertTrue(row[1])
        self.assertEqual(row[2], 1)

    def test_mixed_tools_correct_counts_and_is_mcp(self):
        """Mixed tools → correct counts, monotonic call_seq, correct is_mcp flags."""
        # sorted order: Bash before mcp__pal__thinkdeep
        entry = _make_entry(
            tool_calls={"Bash": 2, "mcp__pal__thinkdeep": 3},
            agent="architect",
        )
        _upsert(self._db, entry)
        self.assertEqual(_count_rows(self._db), 5)

        rows = self._db.execute(
            "SELECT tool_name, is_mcp, call_seq, agent_name FROM tool_calls ORDER BY call_seq"
        ).fetchall()
        # Sorted: Bash (2 rows), mcp__pal__thinkdeep (3 rows)
        self.assertEqual(rows[0][0], "Bash")
        self.assertFalse(rows[0][1])
        self.assertEqual(rows[0][2], 1)

        self.assertEqual(rows[1][0], "Bash")
        self.assertFalse(rows[1][1])
        self.assertEqual(rows[1][2], 2)

        self.assertEqual(rows[2][0], "mcp__pal__thinkdeep")
        self.assertTrue(rows[2][1])
        self.assertEqual(rows[2][2], 3)

        self.assertEqual(rows[4][2], 5)  # last call_seq is 5

        # agent_name denormalized from entry
        for row in rows:
            self.assertEqual(row[3], "architect")

    def test_agent_name_denormalized(self):
        """agent_name from the step entry is written to every tool_calls row."""
        entry = _make_entry(tool_calls={"Read": 2}, agent="specifier")
        _upsert(self._db, entry)
        rows = self._db.execute("SELECT agent_name FROM tool_calls").fetchall()
        for (agent_name,) in rows:
            self.assertEqual(agent_name, "specifier")

    def test_idempotency_retry_with_fewer_tools(self):
        """Retry with fewer tools doesn't leave orphan rows."""
        # First upsert: 5 tool calls
        entry_v1 = _make_entry(tool_calls={"Bash": 3, "Read": 2})
        _upsert(self._db, entry_v1)
        self.assertEqual(_count_rows(self._db), 5)

        # Retry with only 1 tool call — should replace, not accumulate
        entry_v2 = _make_entry(tool_calls={"Read": 1})
        _upsert(self._db, entry_v2)
        self.assertEqual(_count_rows(self._db), 1)

    def test_idempotency_same_upsert_twice(self):
        """Same upsert twice → same row count (no duplicates)."""
        entry = _make_entry(tool_calls={"Bash": 3})
        _upsert(self._db, entry)
        _upsert(self._db, entry)
        self.assertEqual(_count_rows(self._db), 3)

    def test_ac1_bash2_mcp1_three_rows(self):
        """AC-1: Bash:2, mcp__pal__thinkdeep:1 → 3 rows, correct is_mcp, correct call_seq."""
        entry = _make_entry(
            tool_calls={"Bash": 2, "mcp__pal__thinkdeep": 1},
            agent="developer",
        )
        _upsert(self._db, entry)
        self.assertEqual(_count_rows(self._db), 3)

        rows = self._db.execute(
            "SELECT tool_name, is_mcp, call_seq FROM tool_calls ORDER BY call_seq"
        ).fetchall()

        # sorted: Bash < mcp__pal__thinkdeep alphabetically
        bash_rows = [(t, m, s) for t, m, s in rows if t == "Bash"]
        mcp_rows = [(t, m, s) for t, m, s in rows if t == "mcp__pal__thinkdeep"]

        self.assertEqual(len(bash_rows), 2)
        self.assertEqual(len(mcp_rows), 1)
        for _, is_mcp, _ in bash_rows:
            self.assertFalse(is_mcp)
        for _, is_mcp, _ in mcp_rows:
            self.assertTrue(is_mcp)

        # call_seq for Bash should be 1, 2
        bash_seqs = sorted(s for _, _, s in bash_rows)
        self.assertEqual(bash_seqs, [1, 2])
        # call_seq for mcp should be 3
        self.assertEqual(mcp_rows[0][2], 3)

        # agent_name denormalized
        agent_names = self._db.execute("SELECT DISTINCT agent_name FROM tool_calls").fetchall()
        self.assertEqual(len(agent_names), 1)
        self.assertEqual(agent_names[0][0], "developer")

    def test_nullable_per_call_fields(self):
        """input_tokens, output_tokens, cost_usd, duration_ms, called_at are NULL."""
        entry = _make_entry(tool_calls={"Bash": 1})
        _upsert(self._db, entry)
        row = self._db.execute(
            "SELECT input_tokens, output_tokens, cost_usd, duration_ms, called_at FROM tool_calls"
        ).fetchone()
        for val in row:
            self.assertIsNone(val, f"Expected NULL but got {val!r}")


if __name__ == "__main__":
    unittest.main()
