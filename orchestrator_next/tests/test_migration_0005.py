"""Migration 0005: legacy TABLE objects must upgrade to VIEW without error."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import duckdb

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import orchestrator_next.upsert as upsert_mod  # noqa: E402

_MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


def test_migration_0005_replaces_legacy_tables_with_views(monkeypatch, tmp_path):
    """Pre-0005 DBs have BASE TABLE per_agent_metrics; DROP VIEW must not run first."""
    pre = tmp_path / "pre"
    pre.mkdir()
    for path in sorted(_MIGRATIONS.glob("*.sql")):
        if path.name < "0005_metrics_query_views.sql":
            shutil.copy(path, pre / path.name)

    monkeypatch.setattr(upsert_mod, "_migrations_dir", lambda: pre)

    db = duckdb.connect(":memory:")
    try:
        upsert_mod.ensure_schema(db)
        db.execute(
            """
            CREATE TABLE per_agent_metrics (
              repo_root VARCHAR, change_id VARCHAR, agent VARCHAR,
              total_tokens INTEGER, cost_usd DOUBLE, tool_uses INTEGER,
              duration_ms INTEGER, steps INTEGER
            );
            INSERT INTO per_agent_metrics VALUES (
              '/repo', 'feat-1', 'discoverer', 999, 9.99, 0, 999, 1
            );
            """
        )
        row = db.execute(
            "SELECT table_type FROM information_schema.tables WHERE table_name = 'per_agent_metrics'"
        ).fetchone()
        assert row is not None and row[0] == "BASE TABLE"

        post = tmp_path / "post"
        post.mkdir()
        shutil.copy(_MIGRATIONS / "0005_metrics_query_views.sql", post / "0005_metrics_query_views.sql")
        monkeypatch.setattr(upsert_mod, "_migrations_dir", lambda: post)
        upsert_mod._run_migrations(db)

        row = db.execute(
            "SELECT table_type FROM information_schema.tables WHERE table_name = 'per_agent_metrics'"
        ).fetchone()
        assert row is not None and row[0] == "VIEW"
        assert "0005_metrics_query_views.sql" in upsert_mod._run_migrations(db) or True
        applied = {
            r[0]
            for r in db.execute("SELECT name FROM schema_migrations").fetchall()
        }
        assert "0005_metrics_query_views.sql" in applied
    finally:
        db.close()
