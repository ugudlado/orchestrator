"""
T-5 (RED) / T-6 (GREEN): feature_metrics DDL + idempotent migration + upsert.

Source of truth for column names: design.md §Components #3 (DDL block).

Tests:
  (a) After ensure_schema() on a fresh DuckDB file, DESCRIBE feature_metrics
      includes all columns defined in design.md §Components #3.
  (b) Calling ensure_schema() twice is idempotent (no errors, table unchanged).
  (c) upsert_feature_metrics(con, repo_root, change_id, **fields) with
      INSERT OR REPLACE keyed on (repo_root, change_id) replaces an existing row.
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
# Canonical column set from design.md §Components #3 DDL
# (all columns except DEFAULT-only computed_at which DuckDB still DESCRIBES)
# ---------------------------------------------------------------------------

_EXPECTED_COLUMNS = {
    "repo_root",
    "change_id",
    "schema_name",
    # Resolution
    "tasks_total",
    "tasks_planned",
    "tasks_added",
    "tasks_completed",
    "tasks_failed",
    "resolve_rate",
    "pass_at_1",
    "pass_at_2",
    "regressions",
    "regression_rate",
    # Retries / interventions
    "retries_total",
    "human_interventions",
    # Churn
    "files_changed",
    "insertions",
    "deletions",
    "total_commits",
    "rework_commits",
    "rework_rate",
    # Reviews
    "review_scores_json",
    "review_score_avg",
    # Timing
    "wall_clock_minutes",
    # Audit
    "source",
    "computed_at",
}


def _fm_column_names(db) -> set:
    """Return set of column names from DESCRIBE feature_metrics."""
    rows = db.execute("DESCRIBE feature_metrics").fetchall()
    return {row[0] for row in rows}


# ---------------------------------------------------------------------------
# (a) Fresh DB: ensure_schema() creates feature_metrics with all columns
# ---------------------------------------------------------------------------

def test_ensure_schema_creates_feature_metrics_with_all_columns():
    """
    (a) After ensure_schema() on a fresh in-memory DB, DESCRIBE feature_metrics
    must include all columns defined in design.md §Components #3.
    """
    from orchestrator_next.upsert import ensure_schema

    db = duckdb.connect(":memory:")
    try:
        ensure_schema(db)
        cols = _fm_column_names(db)
        missing = _EXPECTED_COLUMNS - cols
        assert not missing, (
            f"feature_metrics missing columns after ensure_schema(): {sorted(missing)}\n"
            f"Present columns: {sorted(cols)}"
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# (b) Idempotency: calling ensure_schema() twice raises no error
# ---------------------------------------------------------------------------

def test_ensure_schema_idempotent():
    """
    (b) Calling ensure_schema() twice on the same DB is idempotent — no errors,
    table schema unchanged.
    """
    from orchestrator_next.upsert import ensure_schema

    db = duckdb.connect(":memory:")
    try:
        ensure_schema(db)
        cols_first = _fm_column_names(db)

        # Second call must not raise and must leave schema unchanged
        ensure_schema(db)
        cols_second = _fm_column_names(db)

        assert cols_first == cols_second, (
            f"Column set changed after second ensure_schema() call.\n"
            f"Before: {sorted(cols_first)}\nAfter: {sorted(cols_second)}"
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# (c) upsert_feature_metrics INSERT OR REPLACE keyed on (repo_root, change_id)
# ---------------------------------------------------------------------------

def test_upsert_feature_metrics_insert_or_replace():
    """
    (c) upsert_feature_metrics(con, repo_root, change_id, **fields) inserts a row,
    and calling it again with the same (repo_root, change_id) but different field
    values replaces the row (exactly one row; updated fields visible).
    """
    from orchestrator_next.upsert import ensure_schema, upsert_feature_metrics

    db = duckdb.connect(":memory:")
    try:
        ensure_schema(db)

        repo_root = "/test/repo"
        change_id = "test-feature"

        # First upsert
        upsert_feature_metrics(
            db,
            repo_root,
            change_id,
            tasks_total=10,
            tasks_completed=8,
            resolve_rate=0.8,
            wall_clock_minutes=30.0,
        )

        count = db.execute(
            "SELECT COUNT(*) FROM feature_metrics WHERE repo_root = ? AND change_id = ?",
            [repo_root, change_id],
        ).fetchone()[0]
        assert count == 1, f"Expected 1 row after first upsert, got {count}"

        row = db.execute(
            "SELECT tasks_total, resolve_rate FROM feature_metrics "
            "WHERE repo_root = ? AND change_id = ?",
            [repo_root, change_id],
        ).fetchone()
        assert row[0] == 10, f"Expected tasks_total=10, got {row[0]!r}"
        assert abs(row[1] - 0.8) < 1e-9, f"Expected resolve_rate=0.8, got {row[1]!r}"

        # Second upsert with same key but different values — must replace
        upsert_feature_metrics(
            db,
            repo_root,
            change_id,
            tasks_total=15,
            tasks_completed=14,
            resolve_rate=0.933,
            wall_clock_minutes=45.0,
        )

        count2 = db.execute(
            "SELECT COUNT(*) FROM feature_metrics WHERE repo_root = ? AND change_id = ?",
            [repo_root, change_id],
        ).fetchone()[0]
        assert count2 == 1, f"Expected 1 row after replace, got {count2} (INSERT OR REPLACE failed)"

        row2 = db.execute(
            "SELECT tasks_total, resolve_rate FROM feature_metrics "
            "WHERE repo_root = ? AND change_id = ?",
            [repo_root, change_id],
        ).fetchone()
        assert row2[0] == 15, f"Expected tasks_total=15 after replace, got {row2[0]!r}"
        assert abs(row2[1] - 0.933) < 1e-9, f"Expected resolve_rate=0.933 after replace, got {row2[1]!r}"

    finally:
        db.close()
