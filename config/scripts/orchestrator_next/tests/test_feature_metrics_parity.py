"""T-9 (RED written before Stage A) / T-10 (GREEN) / T-15 (snapshot-swap after Stage B).

Parity test for the absorbed feature_metrics path.

After Stage B (T-14), the legacy ingest-feature-metrics.py script is deleted.
This test (T-15) compares the absorbed path's output against a pre-captured
JSON snapshot recorded during T-10 (before the legacy script was deleted).

Snapshot file: tests/fixtures/feature_metrics_expected.json
Fixture: spec/changes/archive/2026-04-25-done-verb-level-aware-writes/

Per cycle-20 rule: shape/value parity against a real payload from prior implementation.
The snapshot was captured by running both legacy and absorbed paths against the
fixture and verifying they matched; the snapshot represents the shared output.
"""
from __future__ import annotations

import json
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

from orchestrator_next.record import _resolve_feature_metrics, _write_feature_metrics  # noqa: E402
from orchestrator_next.upsert import ensure_schema  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

_ORCHESTRATOR_HOME = os.environ.get(
    "ORCHESTRATOR_HOME",
    str(Path(_HERE).parent.parent.parent.parent),
)
_ARCHIVE_DIR = Path(_ORCHESTRATOR_HOME) / "spec" / "changes" / "archive" / "2026-04-25-done-verb-level-aware-writes"
_FIXTURE_STATE = _ARCHIVE_DIR / "state.yaml"
_FIXTURE_TASKS = _ARCHIVE_DIR / "tasks.md"
_SNAPSHOT_FILE = Path(_HERE) / "fixtures" / "feature_metrics_expected.json"

# The 24 non-audit columns to compare (excludes source and computed_at per FR-6/design.md)
_PARITY_COLUMNS = [
    "repo_root", "change_id", "schema_name",
    "tasks_total", "tasks_planned", "tasks_added", "tasks_completed", "tasks_failed",
    "resolve_rate",
    "pass_at_1", "pass_at_2", "regressions", "regression_rate",
    "retries_total", "human_interventions",
    "files_changed", "insertions", "deletions", "total_commits",
    "rework_commits", "rework_rate",
    "review_scores_json", "review_score_avg",
    "wall_clock_minutes",
]


def _load_patched_state(state_path: Path, tasks_path: Path) -> dict:
    """Load fixture state and patch missing top-level fields.

    The archive fixture is missing top-level `started_at` (it was set per-step,
    not on the root state dict in this version). Patch it from the first
    step_history entry so both implementations see valid input.
    """
    with open(state_path) as f:
        state = yaml.safe_load(f) or {}

    # Patch started_at from first step_history entry if missing at root
    if not state.get("started_at"):
        history = state.get("step_history") or []
        if history and isinstance(history[0], dict):
            state["started_at"] = history[0].get("started_at")

    # Set tasks_path to the fixture's tasks.md (archive has no .state/<slug>/ dir)
    state["tasks_path"] = str(tasks_path)

    return state


def _fetch_row_as_dict(db, column_names: list) -> dict | None:
    """Fetch the first feature_metrics row and return as a column→value dict."""
    rows = db.execute("SELECT * FROM feature_metrics").fetchall()
    if not rows:
        return None
    col_info = db.execute("DESCRIBE feature_metrics").fetchall()
    all_cols = [c[0] for c in col_info]
    row_dict = dict(zip(all_cols, rows[0]))
    return {col: row_dict.get(col) for col in column_names}


def test_fixture_and_snapshot_exist():
    """Sanity check: the archive fixture and expected snapshot are present."""
    assert _FIXTURE_STATE.exists(), f"Fixture state.yaml missing at {_FIXTURE_STATE}"
    assert _FIXTURE_TASKS.exists(), f"Fixture tasks.md missing at {_FIXTURE_TASKS}"
    assert _SNAPSHOT_FILE.exists(), f"Snapshot file missing at {_SNAPSHOT_FILE}"


def test_parity_against_snapshot(tmp_path):
    """Absorbed path produces identical values to the pre-captured snapshot."""
    # --- Load expected snapshot ---
    with open(_SNAPSHOT_FILE) as f:
        expected_row = json.load(f)

    # --- Load and patch state ---
    state = _load_patched_state(_FIXTURE_STATE, _FIXTURE_TASKS)
    change_id = state["change_id"]  # "done-verb-level-aware-writes"
    repo_root = state.get("repo_root", "")

    # --- Run absorbed path ---
    db = duckdb.connect(":memory:")
    ensure_schema(db)

    fm_data = _resolve_feature_metrics(state, change_id)
    _write_feature_metrics(db, repo_root, change_id, fm_data)

    actual_row = _fetch_row_as_dict(db, _PARITY_COLUMNS)
    db.close()
    assert actual_row is not None, "Absorbed path produced no feature_metrics row"

    # --- Assert parity against snapshot ---
    mismatches = []
    for col in _PARITY_COLUMNS:
        expected_val = expected_row.get(col)
        actual_val = actual_row.get(col)
        if expected_val != actual_val:
            mismatches.append(
                f"  {col!r}: expected={expected_val!r}  actual={actual_val!r}"
            )

    assert not mismatches, (
        f"Parity mismatch between absorbed path and snapshot:\n"
        + "\n".join(mismatches)
    )
