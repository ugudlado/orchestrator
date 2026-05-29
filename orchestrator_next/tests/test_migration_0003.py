"""T-1 (RED) / T-2 (GREEN): Migration 0003 — phase_events and driver_sessions tables.

Tests cover FR-3, AC-9:
  (a) phase_events table created with the exact columns from design.md
  (b) driver_sessions table created with the exact columns from design.md
  (c) Re-running ensure_schema is a no-op (idempotent; exactly one schema_migrations row)
  (d) 0003_phase_events_driver_sessions.sql recorded in schema_migrations exactly once

Design: use ensure_schema(db) on a fresh in-memory DuckDB so the REAL migrations dir
is used. This verifies the file exists and the DDL is syntactically valid.
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


# Expected column names (not types — DuckDB type names vary slightly by version)
# derived from design.md § Components 3.

_EXPECTED_PHASE_EVENTS_COLUMNS: set[str] = {
    "repo_root",
    "change_id",
    "phase",
    "attempt",
    "step_count",
    "cost_usd",
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "duration_ms",
    "started_at",
    "ended_at",
    "upserted_at",
}

_EXPECTED_DRIVER_SESSIONS_COLUMNS: set[str] = {
    "repo_root",
    "change_id",
    "session_id",
    "model",
    "total_tokens",
    "input_tokens",
    "output_tokens",
    "cost_usd",
    "started_at",
    "ended_at",
    "upserted_at",
}

_MIGRATION_NAME = "0003_phase_events_driver_sessions.sql"


def _fresh_db():
    """Open in-memory DB, run ensure_schema with real migrations dir, return connection."""
    import orchestrator_next.upsert as upsert_mod
    db = duckdb.connect(":memory:")
    upsert_mod.ensure_schema(db)
    return db


def _describe_columns(db, table: str) -> set[str]:
    rows = db.execute(f"DESCRIBE {table}").fetchall()
    return {row[0] for row in rows}


# ---------------------------------------------------------------------------
# (a) phase_events table has expected columns
# ---------------------------------------------------------------------------

def test_phase_events_table_created_with_expected_columns():
    """ensure_schema creates phase_events with all columns from design.md."""
    db = _fresh_db()
    try:
        actual = _describe_columns(db, "phase_events")
        assert actual == _EXPECTED_PHASE_EVENTS_COLUMNS, (
            f"phase_events column mismatch.\n"
            f"Extra:   {actual - _EXPECTED_PHASE_EVENTS_COLUMNS}\n"
            f"Missing: {_EXPECTED_PHASE_EVENTS_COLUMNS - actual}"
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# (b) driver_sessions table has expected columns
# ---------------------------------------------------------------------------

def test_driver_sessions_table_created_with_expected_columns():
    """ensure_schema creates driver_sessions with all columns from design.md."""
    db = _fresh_db()
    try:
        actual = _describe_columns(db, "driver_sessions")
        assert actual == _EXPECTED_DRIVER_SESSIONS_COLUMNS, (
            f"driver_sessions column mismatch.\n"
            f"Extra:   {actual - _EXPECTED_DRIVER_SESSIONS_COLUMNS}\n"
            f"Missing: {_EXPECTED_DRIVER_SESSIONS_COLUMNS - actual}"
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# (c) ensure_schema called twice: no-op (idempotent)
# ---------------------------------------------------------------------------

def test_ensure_schema_twice_is_idempotent():
    """Calling ensure_schema twice does not create duplicate schema_migrations rows."""
    import orchestrator_next.upsert as upsert_mod
    db = duckdb.connect(":memory:")
    try:
        upsert_mod.ensure_schema(db)
        upsert_mod.ensure_schema(db)

        rows = db.execute(
            "SELECT name FROM schema_migrations WHERE name = ?",
            [_MIGRATION_NAME],
        ).fetchall()
        assert len(rows) == 1, (
            f"Expected exactly 1 row in schema_migrations for {_MIGRATION_NAME}, "
            f"got {len(rows)} after two ensure_schema calls"
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# (d) 0003_phase_events_driver_sessions.sql recorded in schema_migrations
# ---------------------------------------------------------------------------

def test_migration_0003_recorded_in_schema_migrations():
    """0003_phase_events_driver_sessions.sql appears in schema_migrations after ensure_schema."""
    db = _fresh_db()
    try:
        row = db.execute(
            "SELECT name FROM schema_migrations WHERE name = ?",
            [_MIGRATION_NAME],
        ).fetchone()
        assert row is not None, (
            f"{_MIGRATION_NAME} not found in schema_migrations. "
            f"The migration file must exist and be applied by ensure_schema."
        )
        assert row[0] == _MIGRATION_NAME
    finally:
        db.close()
