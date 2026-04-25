"""T-9 (RED) / T-10 (GREEN): Parity test against done-verb-level-aware-writes fixture.

Runs BOTH the legacy ingest-feature-metrics.py script AND the absorbed
_resolve_feature_metrics + _write_feature_metrics path against the archived
fixture, then asserts equal across 24 non-audit columns (source excluded).

Per cycle-20 rule: shape/value parity against at least one real payload.

After T-14 (legacy script deleted), T-15 rewrites this test to compare against
a captured JSON snapshot instead of re-running the legacy script.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
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
_LEGACY_SCRIPT = Path(_ORCHESTRATOR_HOME) / "scripts" / "inline" / "ingest-feature-metrics.py"

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


@pytest.fixture(scope="module")
def tmp_dir_module(tmp_path_factory):
    return tmp_path_factory.mktemp("parity")


def test_fixture_exists():
    """Sanity check: the archive fixture and legacy script are present."""
    assert _FIXTURE_STATE.exists(), f"Fixture state.yaml missing at {_FIXTURE_STATE}"
    assert _FIXTURE_TASKS.exists(), f"Fixture tasks.md missing at {_FIXTURE_TASKS}"
    assert _LEGACY_SCRIPT.exists(), f"Legacy script missing at {_LEGACY_SCRIPT}"


def test_parity_between_legacy_and_absorbed(tmp_path):
    """Both implementations produce identical values for 24 non-audit columns."""
    # --- Load and patch state ---
    state = _load_patched_state(_FIXTURE_STATE, _FIXTURE_TASKS)
    change_id = state["change_id"]  # "done-verb-level-aware-writes"
    repo_root = state.get("repo_root", "")

    # Write patched state to tmp so legacy script can read it
    patched_state_path = tmp_path / "state.yaml"
    with open(patched_state_path, "w") as f:
        yaml.safe_dump(state, f, sort_keys=False)

    # --- Path A: legacy script ---
    legacy_db_path = tmp_path / "legacy.duckdb"
    legacy_db = duckdb.connect(str(legacy_db_path))
    ensure_schema(legacy_db)
    legacy_db.close()

    env_a = {
        **os.environ,
        "ORCHESTRATOR_HOME": _ORCHESTRATOR_HOME,
        "METRICS_DB": str(legacy_db_path),
    }
    proc = subprocess.run(
        [sys.executable, str(_LEGACY_SCRIPT), str(patched_state_path)],
        capture_output=True, text=True, env=env_a,
    )
    assert proc.returncode == 0, (
        f"Legacy script failed (exit {proc.returncode}):\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )

    legacy_db = duckdb.connect(str(legacy_db_path))
    legacy_row = _fetch_row_as_dict(legacy_db, _PARITY_COLUMNS)
    legacy_db.close()
    assert legacy_row is not None, "Legacy script produced no feature_metrics row"

    # --- Path B: absorbed path ---
    absorbed_db_path = tmp_path / "absorbed.duckdb"
    absorbed_db = duckdb.connect(str(absorbed_db_path))
    ensure_schema(absorbed_db)

    fm_data = _resolve_feature_metrics(state, change_id)
    # Override repo_root and change_id in fm_data aren't needed — they're passed separately
    _write_feature_metrics(absorbed_db, repo_root, change_id, fm_data)

    absorbed_row = _fetch_row_as_dict(absorbed_db, _PARITY_COLUMNS)
    absorbed_db.close()
    assert absorbed_row is not None, "Absorbed path produced no feature_metrics row"

    # --- Assert parity ---
    mismatches = []
    for col in _PARITY_COLUMNS:
        legacy_val = legacy_row[col]
        absorbed_val = absorbed_row[col]
        if legacy_val != absorbed_val:
            mismatches.append(
                f"  {col!r}: legacy={legacy_val!r}  absorbed={absorbed_val!r}"
            )

    assert not mismatches, (
        f"Parity mismatch between legacy script and absorbed path:\n"
        + "\n".join(mismatches)
    )
