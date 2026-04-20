"""
T-1 (RED) / T-2 (GREEN): _run_migrations(db) in upsert.py.

Tests cover FR-1 and NFR-2 (idempotence):
  (a) fresh DB → schema_migrations table created, no rows applied
  (b) runner applies a test SQL file and records the name in schema_migrations
  (c) running twice is a no-op (idempotent — no duplicate rows, no re-exec)
  (d) broken SQL raises and schema_migrations stays unchanged (per-file ROLLBACK)
  (e) adding a second file later → only the new file applied on next call

Design: tests monkeypatch orchestrator_next.upsert._migrations_dir (the module-
level helper) to point at a tmp_path-backed directory so the test suite is
isolated from the real migrations/ directory and future migration files.

T-3 (RED) / T-4 (GREEN): seed migration 0001_seed_pricing.sql.

Tests cover FR-2, NFR-5 (step_events unchanged):
  (a) after ensure_schema on a fresh DB, DESCRIBE pricing returns exactly the
      spec columns with correct DuckDB types
  (b) SELECT COUNT(*) FROM pricing equals len(pricing.yaml models) + 1 for __default__
  (c) spot-check claude-sonnet-4-6: input=3.00, output=15.00, cache_read=0.30,
      cache_creation=3.75
  (d) is_local=TRUE for coder; is_local=FALSE for all others
  (e) DESCRIBE step_events returns unchanged column set (NFR-5 / AC-9 invariant)

Design: T-3 scenarios use the REAL migrations dir (no monkeypatch on _migrations_dir)
so that ensure_schema(db) picks up 0001_seed_pricing.sql from its actual location.
T-1 scenarios keep their tmp_path monkeypatch; the two regimes are independent.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import duckdb
import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _applied_names(db) -> list[str]:
    """Return sorted list of migration names recorded in schema_migrations."""
    rows = db.execute("SELECT name FROM schema_migrations ORDER BY name").fetchall()
    return [r[0] for r in rows]


def _table_exists(db, table_name: str) -> bool:
    rows = db.execute(
        "SELECT table_name FROM duckdb_tables() WHERE table_name = ?",
        [table_name],
    ).fetchall()
    return len(rows) > 0


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_fresh_db_creates_tracking_table_with_no_rows(monkeypatch, tmp_path):
    """(a) Fresh DB: schema_migrations is created; no rows (empty migrations dir)."""
    import orchestrator_next.upsert as upsert_mod

    monkeypatch.setattr(upsert_mod, "_migrations_dir", lambda: tmp_path)

    db = duckdb.connect(":memory:")
    try:
        upsert_mod._run_migrations(db)

        assert _table_exists(db, "schema_migrations"), (
            "schema_migrations table must be created by _run_migrations"
        )
        assert _applied_names(db) == [], (
            "No migrations in tmp dir — schema_migrations must be empty"
        )
    finally:
        db.close()


def test_applies_sql_file_and_records_name(monkeypatch, tmp_path):
    """(b) Runner applies a test 0001_noop.sql and records its name."""
    import orchestrator_next.upsert as upsert_mod

    # Create a minimal no-op SQL file
    noop = tmp_path / "0001_noop.sql"
    noop.write_text("CREATE TABLE IF NOT EXISTS _noop_b (id INTEGER);\n")

    monkeypatch.setattr(upsert_mod, "_migrations_dir", lambda: tmp_path)

    db = duckdb.connect(":memory:")
    try:
        result = upsert_mod._run_migrations(db)

        assert result == ["0001_noop.sql"], (
            "_run_migrations should return list of applied migration names"
        )
        assert _applied_names(db) == ["0001_noop.sql"], (
            "schema_migrations must contain the applied file name"
        )
        # The noop table was actually executed
        assert _table_exists(db, "_noop_b"), (
            "The SQL in 0001_noop.sql should have been executed"
        )
    finally:
        db.close()


def test_second_call_is_noop(monkeypatch, tmp_path):
    """(c) Running twice is a no-op: no duplicate rows, second call returns []."""
    import orchestrator_next.upsert as upsert_mod

    noop = tmp_path / "0001_noop.sql"
    noop.write_text("CREATE TABLE IF NOT EXISTS _noop_c (id INTEGER);\n")

    monkeypatch.setattr(upsert_mod, "_migrations_dir", lambda: tmp_path)

    db = duckdb.connect(":memory:")
    try:
        first = upsert_mod._run_migrations(db)
        second = upsert_mod._run_migrations(db)

        assert first == ["0001_noop.sql"]
        assert second == [], "Second call must be no-op — all migrations already applied"
        assert _applied_names(db) == ["0001_noop.sql"], (
            "Exactly one row in schema_migrations after two runs"
        )
    finally:
        db.close()


def test_broken_sql_raises_and_tracking_table_unchanged(monkeypatch, tmp_path):
    """(d) Broken SQL raises; schema_migrations stays unchanged (transactional)."""
    import orchestrator_next.upsert as upsert_mod

    broken = tmp_path / "0001_broken.sql"
    broken.write_text("THIS IS NOT VALID SQL;\n")

    monkeypatch.setattr(upsert_mod, "_migrations_dir", lambda: tmp_path)

    db = duckdb.connect(":memory:")
    try:
        with pytest.raises(Exception):
            upsert_mod._run_migrations(db)

        # schema_migrations table must exist (it is created before the file loop)
        assert _table_exists(db, "schema_migrations"), (
            "schema_migrations should exist even after a failed migration"
        )
        # But no name must be recorded — ROLLBACK prevents partial recording
        assert _applied_names(db) == [], (
            "schema_migrations must have zero rows after a rolled-back migration"
        )
    finally:
        db.close()


def test_incremental_apply_only_new_file(monkeypatch, tmp_path):
    """(e) Adding 0002_noop.sql later: only 0002 applied on next call."""
    import orchestrator_next.upsert as upsert_mod

    noop1 = tmp_path / "0001_noop.sql"
    noop1.write_text("CREATE TABLE IF NOT EXISTS _noop_e1 (id INTEGER);\n")

    monkeypatch.setattr(upsert_mod, "_migrations_dir", lambda: tmp_path)

    db = duckdb.connect(":memory:")
    try:
        first = upsert_mod._run_migrations(db)
        assert first == ["0001_noop.sql"]

        # Now add a second migration file
        noop2 = tmp_path / "0002_noop.sql"
        noop2.write_text("CREATE TABLE IF NOT EXISTS _noop_e2 (id INTEGER);\n")

        second = upsert_mod._run_migrations(db)
        assert second == ["0002_noop.sql"], (
            "Second call should apply only the new 0002_noop.sql"
        )
        assert _applied_names(db) == ["0001_noop.sql", "0002_noop.sql"], (
            "Both migrations must be recorded after two incremental calls"
        )
        assert _table_exists(db, "_noop_e2"), (
            "SQL in 0002_noop.sql must have been executed"
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# T-3 scenarios — seed migration (use REAL migrations dir via ensure_schema)
# ---------------------------------------------------------------------------

# Path to config/pricing.yaml — resolve from test file location.
# tests/ → orchestrator_next/ → scripts/ → config/
_PRICING_YAML = Path(_HERE).parents[3] / "config" / "pricing.yaml"

# Expected pricing table columns and their DuckDB types (from design.md § Components 2).
# Assert as a dict so a failure prints the full column-name/type diff.
_EXPECTED_PRICING_COLUMNS: dict[str, str] = {
    "model_id": "VARCHAR",
    "input_usd": "DOUBLE",
    "output_usd": "DOUBLE",
    "cache_read_usd": "DOUBLE",
    "cache_creation_usd": "DOUBLE",
    "is_local": "BOOLEAN",
    "effective_from": "TIMESTAMP",
}

# Expected step_events column names (NFR-5 / AC-9 invariant).
# Source of truth: _DDL_STEP_EVENTS in upsert.py.
_EXPECTED_STEP_EVENTS_COLUMNS: set[str] = {
    "repo_root",
    "change_id",
    "phase",
    "step_id",
    "attempt",
    "agent_name",
    "agent_id",
    "status",
    "schema_name",
    "started_at",
    "ended_at",
    "duration_ms",
    "model",
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "cost_usd",
    "turns",
    "tool_calls_json",
    "artifacts_json",
    "escalation_json",
    "upserted_at",
}


def _fresh_seeded_db():
    """Open an in-memory DB, run ensure_schema (which applies real seed migrations),
    and return the open connection. Caller is responsible for closing it."""
    import orchestrator_next.upsert as upsert_mod

    db = duckdb.connect(":memory:")
    upsert_mod.ensure_schema(db)
    return db


def test_pricing_table_describe_matches_spec():
    """(a) DESCRIBE pricing returns exactly the columns and types from the spec."""
    db = _fresh_seeded_db()
    try:
        rows = db.execute("DESCRIBE pricing").fetchall()
        # DESCRIBE returns (column_name, column_type, null, key, default, extra)
        actual = {row[0]: row[1] for row in rows}
        assert actual == _EXPECTED_PRICING_COLUMNS, (
            f"pricing column types mismatch.\n"
            f"Expected: {_EXPECTED_PRICING_COLUMNS}\n"
            f"Actual:   {actual}"
        )
    finally:
        db.close()


def test_pricing_row_count_matches_yaml():
    """(b) Row count == number of models in pricing.yaml + 1 for __default__."""
    with open(_PRICING_YAML) as fh:
        doc = yaml.safe_load(fh)
    expected_count = len(doc["models"]) + 1  # +1 for __default__

    db = _fresh_seeded_db()
    try:
        row = db.execute("SELECT COUNT(*) FROM pricing").fetchone()
        actual_count = row[0]
        assert actual_count == expected_count, (
            f"Expected {expected_count} rows in pricing "
            f"(yaml models={len(doc['models'])} + 1 for __default__), "
            f"got {actual_count}"
        )
    finally:
        db.close()


def test_pricing_sonnet_spot_check():
    """(c) claude-sonnet-4-6 has the expected rates."""
    db = _fresh_seeded_db()
    try:
        row = db.execute(
            "SELECT input_usd, output_usd, cache_read_usd, cache_creation_usd "
            "FROM pricing WHERE model_id = 'claude-sonnet-4-6'"
        ).fetchone()
        assert row is not None, "claude-sonnet-4-6 row must exist in pricing"
        input_usd, output_usd, cache_read_usd, cache_creation_usd = row
        assert input_usd == 3.00, f"input_usd: expected 3.00, got {input_usd}"
        assert output_usd == 15.00, f"output_usd: expected 15.00, got {output_usd}"
        assert cache_read_usd == 0.30, f"cache_read_usd: expected 0.30, got {cache_read_usd}"
        assert cache_creation_usd == 3.75, (
            f"cache_creation_usd: expected 3.75, got {cache_creation_usd}"
        )
    finally:
        db.close()


def test_pricing_is_local_flag():
    """(d) is_local=TRUE for coder; is_local=FALSE for all other seeded models."""
    db = _fresh_seeded_db()
    try:
        # coder must be local
        row = db.execute(
            "SELECT is_local FROM pricing WHERE model_id = 'coder'"
        ).fetchone()
        assert row is not None, "coder row must exist in pricing"
        assert row[0] is True, f"coder.is_local: expected True, got {row[0]}"

        # Every other row must not be local
        non_local_rows = db.execute(
            "SELECT model_id, is_local FROM pricing WHERE model_id != 'coder' AND is_local = TRUE"
        ).fetchall()
        assert non_local_rows == [], (
            f"Only 'coder' should have is_local=TRUE; found: {[r[0] for r in non_local_rows]}"
        )
    finally:
        db.close()


def test_step_events_columns_unchanged():
    """(e) DESCRIBE step_events returns the same column set as before seed migration
    (NFR-5 / AC-9 — multi-level metrics invariant). This test passes in both RED
    and GREEN as a regression guard."""
    db = _fresh_seeded_db()
    try:
        rows = db.execute("DESCRIBE step_events").fetchall()
        actual_columns = {row[0] for row in rows}
        assert actual_columns == _EXPECTED_STEP_EVENTS_COLUMNS, (
            f"step_events column set changed — NFR-5 violation.\n"
            f"Extra columns: {actual_columns - _EXPECTED_STEP_EVENTS_COLUMNS}\n"
            f"Missing columns: {_EXPECTED_STEP_EVENTS_COLUMNS - actual_columns}"
        )
    finally:
        db.close()
