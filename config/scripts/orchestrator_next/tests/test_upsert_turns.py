"""
T-1 (RED) / T-2 (GREEN): step_events.turns column migration + upsert passthrough.

Tests:
  (a) DESCRIBE step_events includes 'turns' after ensure_schema on a fresh DB.
  (b) upsert_step_event with usage={"turns": 42, ...} writes 42 to the column.
  (c) _migrate_step_events adds 'turns' to a pre-existing legacy table without error.
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

# DDL for a legacy step_events table that does NOT have the turns column.
# Matches the shape from test_upsert_migration.py (plain names, no turns).
_DDL_STEP_EVENTS_NO_TURNS = """
CREATE TABLE step_events (
  repo_root   VARCHAR NOT NULL,
  change_id   VARCHAR NOT NULL,
  phase       VARCHAR NOT NULL,
  step_id     VARCHAR NOT NULL,
  attempt     INTEGER NOT NULL,
  agent_name  VARCHAR NOT NULL,
  agent_id    VARCHAR,
  status      VARCHAR NOT NULL,
  schema_name VARCHAR,
  started_at  TIMESTAMP,
  ended_at    TIMESTAMP,
  duration_ms BIGINT,
  model                        VARCHAR,
  input_tokens                 BIGINT,
  output_tokens                BIGINT,
  cache_read_input_tokens      BIGINT,
  cache_creation_input_tokens  BIGINT,
  cost_usd                     DOUBLE,
  tool_calls_json  VARCHAR,
  artifacts_json   VARCHAR,
  escalation_json  VARCHAR,
  upserted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (repo_root, change_id, phase, step_id, attempt, status)
)
"""


def _column_names(db) -> set:
    """Return the set of column names for step_events."""
    rows = db.execute("DESCRIBE step_events").fetchall()
    return {row[0] for row in rows}


# ---------------------------------------------------------------------------
# (a) Fresh DB: ensure_schema() creates turns column
# ---------------------------------------------------------------------------

def test_ensure_schema_adds_turns_column():
    """
    (a) After ensure_schema() on a fresh in-memory DB, DESCRIBE step_events
    must include 'turns'. This tests the DDL path (column in _DDL_STEP_EVENTS).
    """
    from orchestrator_next.upsert import ensure_schema

    db = duckdb.connect(":memory:")
    try:
        ensure_schema(db)
        cols = _column_names(db)
        assert "turns" in cols, (
            f"'turns' column missing from step_events after ensure_schema(). "
            f"Present columns: {sorted(cols)}"
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# (b) upsert_step_event writes turns value to the column
# ---------------------------------------------------------------------------

def test_upsert_step_event_writes_turns_value():
    """
    (b) upsert_step_event with usage={"turns": 42, ...} writes 42 to the
    'turns' column in step_events.
    """
    from orchestrator_next.upsert import ensure_schema, upsert_step_event
    from orchestrator_next.parser import StepHistoryEntry

    db = duckdb.connect(":memory:")
    try:
        ensure_schema(db)

        # Build a minimal StepHistoryEntry
        entry = StepHistoryEntry(
            step_id="test-step",
            phase="implement",
            status="completed",
            agent="developer",
            attempt=1,
            started_at="2026-04-20T00:00:00Z",
            ended_at="2026-04-20T00:01:00Z",
            usage={
                "turns": 42,
                "input_tokens": 100,
                "output_tokens": 50,
                "model": "claude-sonnet-4-5",
                "cost_usd": 0.001,
            },
            escalation=None,
            raw={},
        )
        context = {"repo_root": "/test/repo", "change_id": "test-feature"}

        upsert_step_event(db, entry, context)

        row = db.execute(
            "SELECT turns FROM step_events WHERE change_id = ? AND step_id = ?",
            ["test-feature", "test-step"],
        ).fetchone()

        assert row is not None, "No row found in step_events after upsert"
        assert row[0] == 42, (
            f"Expected turns=42, got turns={row[0]!r}"
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# (c) _migrate_step_events adds turns to a pre-existing legacy table
# ---------------------------------------------------------------------------

def test_migrate_step_events_adds_turns_column():
    """
    (c) Calling _migrate_step_events on a pre-existing table that lacks 'turns'
    adds the column without error. Idempotent: calling it a second time is also
    safe.
    """
    from orchestrator_next.upsert import _migrate_step_events

    db = duckdb.connect(":memory:")
    try:
        # Seed a legacy table that has plain column names but no 'turns' column
        db.execute(_DDL_STEP_EVENTS_NO_TURNS)

        # Confirm 'turns' is absent before migration
        cols_before = _column_names(db)
        assert "turns" not in cols_before, (
            f"Setup error: 'turns' should not be in legacy table, but was: {cols_before}"
        )

        # Should not raise
        _migrate_step_events(db)

        cols_after = _column_names(db)
        assert "turns" in cols_after, (
            f"'turns' column not added by _migrate_step_events(). "
            f"Present columns: {sorted(cols_after)}"
        )

        # Idempotency: calling again should not raise
        _migrate_step_events(db)
        cols_again = _column_names(db)
        assert "turns" in cols_again, "'turns' column disappeared after second migration call"
    finally:
        db.close()
