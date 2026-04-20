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
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import duckdb
import pytest

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
