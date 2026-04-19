"""
T-1 (RED) / T-2 (GREEN): DuckDB step_events migration fix tests.

Tests:
- migration_renames_otel_columns: seeds table with otel-prefixed column names
  + idx_step_events_change index, calls ensure_schema, asserts columns renamed
  and index recreated.
- migration_idempotent_fast_path: seeds table with already-renamed plain
  columns, verifies ensure_schema does NOT issue DROP INDEX (fast path).
"""
from __future__ import annotations

import os
import sys

import duckdb
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# DDL that mirrors _DDL_STEP_EVENTS but uses otel-prefixed column names
# (the "old" names from _STEP_EVENTS_RENAMES LHS).
_DDL_STEP_EVENTS_OLD = """
CREATE TABLE step_events (
  repo_root   VARCHAR NOT NULL,
  change_id   VARCHAR NOT NULL,
  phase       VARCHAR NOT NULL,
  step_id     VARCHAR NOT NULL,
  attempt     INTEGER NOT NULL,
  agent_name  VARCHAR NOT NULL,
  status      VARCHAR NOT NULL,
  schema_name VARCHAR,
  started_at  TIMESTAMP,
  ended_at    TIMESTAMP,
  duration_ms BIGINT,
  gen_ai_request_model                 VARCHAR,
  gen_ai_usage_input_tokens            BIGINT,
  gen_ai_usage_output_tokens           BIGINT,
  gen_ai_usage_cache_read_input_tokens BIGINT,
  gen_ai_usage_cost_usd                DOUBLE,
  tool_calls_json  VARCHAR,
  artifacts_json   VARCHAR,
  escalation_json  VARCHAR,
  upserted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (repo_root, change_id, phase, step_id, attempt, status)
)
"""

_CREATE_INDEX_OLD = """
CREATE INDEX idx_step_events_change
  ON step_events(repo_root, change_id)
"""


def _column_names(db) -> set:
    """Return the set of column names for step_events."""
    rows = db.execute("DESCRIBE step_events").fetchall()
    return {row[0] for row in rows}


def _index_exists(db) -> bool:
    """Return True if idx_step_events_change exists on step_events."""
    rows = db.execute(
        "SELECT index_name FROM duckdb_indexes() WHERE table_name='step_events'"
    ).fetchall()
    return any(r[0] == "idx_step_events_change" for r in rows)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_migration_renames_otel_columns():
    """
    Seeding step_events with otel-prefixed column names + the migration index,
    then calling ensure_schema() should:
    (a) not raise,
    (b) rename all otel columns to plain names,
    (c) preserve idx_step_events_change.
    """
    from orchestrator_next.upsert import ensure_schema, _STEP_EVENTS_RENAMES

    db = duckdb.connect(":memory:")
    try:
        # Seed old-schema table
        db.execute(_DDL_STEP_EVENTS_OLD)
        db.execute(_CREATE_INDEX_OLD)

        old_cols = _column_names(db)
        # Confirm otel names present before migration
        for old, _ in _STEP_EVENTS_RENAMES:
            assert old in old_cols, f"Setup error: expected otel column '{old}' not in table"

        # (a) should not raise
        ensure_schema(db)

        new_cols = _column_names(db)

        # (b) new plain names present; old otel names gone
        for old, new in _STEP_EVENTS_RENAMES:
            assert new in new_cols, f"Column '{new}' should exist after migration"
            assert old not in new_cols, f"Column '{old}' should be gone after migration"

        # (c) index recreated
        assert _index_exists(db), "idx_step_events_change should exist after migration"
    finally:
        db.close()


def test_migration_idempotent_fast_path():
    """
    When step_events already has plain column names, ensure_schema() must NOT
    issue DROP INDEX (fast path — no unnecessary index churn).

    Because duckdb.DuckDBPyConnection.execute is read-only, we wrap the
    connection in a thin proxy that records SQL calls without replacing the
    native method.
    """
    from orchestrator_next.upsert import ensure_schema

    class TrackingDB:
        """Thin proxy around a DuckDB connection that records SQL strings."""

        def __init__(self, conn):
            self._conn = conn
            self.sql_calls: list[str] = []

        def execute(self, sql, *args, **kwargs):
            self.sql_calls.append(sql)
            return self._conn.execute(sql, *args, **kwargs)

        def close(self):
            return self._conn.close()

    real_conn = duckdb.connect(":memory:")
    try:
        # First call: create table with plain names (no tracking yet)
        ensure_schema(real_conn)

        # Confirm plain columns present (sanity)
        cols = _column_names(real_conn)
        assert "model" in cols
        assert "gen_ai_request_model" not in cols

        # Now wrap with tracking proxy for the second call
        db = TrackingDB(real_conn)
        ensure_schema(db)

        # Assert DROP INDEX was NOT issued
        drop_index_calls = [s for s in db.sql_calls if "DROP INDEX" in s.upper()]
        assert len(drop_index_calls) == 0, (
            f"ensure_schema() issued DROP INDEX on already-migrated table: {drop_index_calls}"
        )
    finally:
        real_conn.close()
