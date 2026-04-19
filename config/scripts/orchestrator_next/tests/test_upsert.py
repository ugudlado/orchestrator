"""
Tests for orchestrator_next.upsert — feature_complexity DDL and upsert function.

T-3 (HL-291): RED tests for ensure_schema() creating feature_complexity table
and upsert_feature_complexity() round-trip semantics.

Tests:
- ensure_schema() creates feature_complexity table on fresh DB
- ensure_schema() is idempotent (second call no-op)
- upsert_feature_complexity() round-trips a row with complexity value
- upsert_feature_complexity() round-trips a row with NULL complexity
- re-upsert replaces existing row (INSERT OR REPLACE semantics)
- upsert_feature_complexity() exists as public API
"""
from __future__ import annotations

import os
import sys

import pytest
import duckdb

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def conn():
    """In-memory DuckDB connection."""
    c = duckdb.connect(":memory:")
    yield c
    c.close()


@pytest.fixture()
def schema_conn(conn):
    """Connection with ensure_schema() already called."""
    from orchestrator_next.upsert import ensure_schema
    ensure_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# T-3 (HL-291): feature_complexity DDL tests
# ---------------------------------------------------------------------------

class TestEnsureSchemaFeatureComplexity:

    def test_feature_complexity_table_created(self, schema_conn):
        """ensure_schema() creates the feature_complexity table (FR-3)."""
        tables = schema_conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name = 'feature_complexity'"
        ).fetchall()
        assert tables, "feature_complexity table was not created"

    def test_ensure_schema_is_idempotent(self, conn):
        """ensure_schema() can be called twice without error (FR-3, IF NOT EXISTS)."""
        from orchestrator_next.upsert import ensure_schema
        ensure_schema(conn)
        # Second call must not raise
        ensure_schema(conn)
        tables = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name = 'feature_complexity'"
        ).fetchall()
        assert tables

    def test_feature_complexity_has_required_columns(self, schema_conn):
        """feature_complexity table has all required columns (FR-3)."""
        cols = schema_conn.execute("DESCRIBE feature_complexity").fetchall()
        col_names = {row[0] for row in cols}
        required = {"repo_root", "change_id", "complexity", "schema_name", "started_at", "completed_at"}
        assert required.issubset(col_names), f"Missing columns: {required - col_names}"

    def test_feature_complexity_has_upserted_at(self, schema_conn):
        """feature_complexity table has upserted_at column with default (FR-3)."""
        cols = schema_conn.execute("DESCRIBE feature_complexity").fetchall()
        col_names = {row[0] for row in cols}
        assert "upserted_at" in col_names

    def test_existing_tables_still_created(self, schema_conn):
        """ensure_schema() still creates step_events and tool_calls tables (non-regression)."""
        tables = schema_conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name IN ('step_events', 'tool_calls')"
        ).fetchall()
        names = {r[0] for r in tables}
        assert "step_events" in names
        assert "tool_calls" in names


# ---------------------------------------------------------------------------
# T-3 (HL-291): upsert_feature_complexity tests
# ---------------------------------------------------------------------------

class TestUpsertFeatureComplexity:

    def test_upsert_feature_complexity_exists(self):
        """upsert_feature_complexity is a public function in upsert module (FR-4)."""
        from orchestrator_next import upsert
        assert hasattr(upsert, "upsert_feature_complexity"), (
            "upsert_feature_complexity not found in upsert module"
        )

    def test_upsert_round_trip_with_complexity(self, schema_conn):
        """upsert_feature_complexity() stores a row; SELECT returns expected values (FR-4)."""
        from orchestrator_next.upsert import upsert_feature_complexity
        upsert_feature_complexity(
            schema_conn,
            repo_root="/home/user/myrepo",
            change_id="my-feature",
            complexity="M",
            schema_name="feature",
            started_at="2026-01-01T00:00:00Z",
            completed_at="2026-01-02T00:00:00Z",
        )
        row = schema_conn.execute(
            "SELECT repo_root, change_id, complexity, schema_name FROM feature_complexity "
            "WHERE repo_root = ? AND change_id = ?",
            ["/home/user/myrepo", "my-feature"],
        ).fetchone()
        assert row is not None
        assert row[0] == "/home/user/myrepo"
        assert row[1] == "my-feature"
        assert row[2] == "M"
        assert row[3] == "feature"

    def test_upsert_null_complexity_row_persists(self, schema_conn):
        """upsert_feature_complexity() with complexity=None still writes a row (FR-4)."""
        from orchestrator_next.upsert import upsert_feature_complexity
        upsert_feature_complexity(
            schema_conn,
            repo_root="/home/user/myrepo",
            change_id="no-complexity",
            complexity=None,
            schema_name="feature",
            started_at=None,
            completed_at=None,
        )
        row = schema_conn.execute(
            "SELECT change_id, complexity FROM feature_complexity "
            "WHERE repo_root = ? AND change_id = ?",
            ["/home/user/myrepo", "no-complexity"],
        ).fetchone()
        assert row is not None, "Row was not written for NULL complexity"
        assert row[0] == "no-complexity"
        assert row[1] is None

    def test_upsert_replace_semantics(self, schema_conn):
        """Re-upserting the same (repo_root, change_id) replaces the previous row (FR-4)."""
        from orchestrator_next.upsert import upsert_feature_complexity
        # First upsert
        upsert_feature_complexity(
            schema_conn,
            repo_root="/home/user/myrepo",
            change_id="my-feature",
            complexity="S",
            schema_name="feature",
            started_at="2026-01-01T00:00:00Z",
            completed_at=None,
        )
        # Second upsert with updated complexity
        upsert_feature_complexity(
            schema_conn,
            repo_root="/home/user/myrepo",
            change_id="my-feature",
            complexity="XL",
            schema_name="feature",
            started_at="2026-01-01T00:00:00Z",
            completed_at="2026-01-03T00:00:00Z",
        )
        rows = schema_conn.execute(
            "SELECT complexity FROM feature_complexity "
            "WHERE repo_root = ? AND change_id = ?",
            ["/home/user/myrepo", "my-feature"],
        ).fetchall()
        assert len(rows) == 1, "Expected exactly one row after re-upsert"
        assert rows[0][0] == "XL"

    def test_upsert_multiple_features(self, schema_conn):
        """Multiple features can be upserted independently (FR-4)."""
        from orchestrator_next.upsert import upsert_feature_complexity
        for cid, cx in [("feat-a", "XS"), ("feat-b", "L"), ("feat-c", None)]:
            upsert_feature_complexity(
                schema_conn,
                repo_root="/home/user/myrepo",
                change_id=cid,
                complexity=cx,
                schema_name="feature",
                started_at=None,
                completed_at=None,
            )
        count = schema_conn.execute(
            "SELECT COUNT(*) FROM feature_complexity WHERE repo_root = ?",
            ["/home/user/myrepo"],
        ).fetchone()[0]
        assert count == 3
