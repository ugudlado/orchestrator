"""T-1 (RED) / T-2 (GREEN): tests for the 6 computation functions ported from
ingest-feature-metrics.py, plus _resolve_feature_metrics and _write_feature_metrics.

Covers FR-1, FR-2, FR-5, AC-3, AC-4.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import duckdb
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from orchestrator_next.record import (  # noqa: E402
    parse_tasks,
    compute_retries,
    compute_resolution,
    run_git_churn,
    extract_review_scores,
    wall_clock_minutes,
)
from orchestrator_next.upsert import ensure_schema  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tasks_md(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "tasks.md"
    p.write_text(content)
    return p


# ---------------------------------------------------------------------------
# T-1a: parse_tasks — counts [x], [ ], [~] markers
# ---------------------------------------------------------------------------

class TestParseTasks:
    def test_counts_all_markers(self, tmp_path):
        p = _make_tasks_md(tmp_path, """
- [x] Task 1 done
- [ ] Task 2 pending
- [~] Task 3 skipped
- [x] Task 4 done
""")
        result = parse_tasks(p)
        assert result["tasks_total"] == 4
        assert result["tasks_completed"] == 2
        assert result["tasks_failed"] == 1   # total - completed - skipped
        assert result["tasks_planned"] == 4
        assert result["tasks_added"] == 0
        assert abs(result["resolve_rate"] - 2 / 4) < 1e-6

    def test_empty_tasks_md(self, tmp_path):
        p = _make_tasks_md(tmp_path, "# No tasks here\n")
        result = parse_tasks(p)
        assert result["tasks_total"] == 0
        assert result["resolve_rate"] == 0.0

    def test_all_completed(self, tmp_path):
        p = _make_tasks_md(tmp_path, "- [x] T1\n- [x] T2\n- [x] T3\n")
        result = parse_tasks(p)
        assert result["tasks_total"] == 3
        assert result["tasks_completed"] == 3
        assert result["tasks_failed"] == 0
        assert result["resolve_rate"] == 1.0

    def test_case_insensitive_x(self, tmp_path):
        p = _make_tasks_md(tmp_path, "- [X] Task 1\n- [x] Task 2\n")
        result = parse_tasks(p)
        assert result["tasks_completed"] == 2

    def test_failed_is_non_negative(self, tmp_path):
        """Even if skipped + completed > total somehow, failed must be >= 0."""
        p = _make_tasks_md(tmp_path, "- [x] T1\n- [~] T2\n")
        result = parse_tasks(p)
        assert result["tasks_failed"] >= 0


# ---------------------------------------------------------------------------
# T-1b: compute_retries — sums state.retries.* and reads human_interventions
# ---------------------------------------------------------------------------

class TestComputeRetries:
    def test_sums_retries(self):
        state = {"retries": {"execute-next-task": 2, "run-phase-review": 1}}
        result = compute_retries(state)
        assert result["retries_total"] == 3
        assert result["human_interventions"] == 0

    def test_reads_human_interventions(self):
        state = {"retries": {}, "human_interventions": 5}
        result = compute_retries(state)
        assert result["human_interventions"] == 5

    def test_absent_retries_returns_zeros(self):
        result = compute_retries({})
        assert result["retries_total"] == 0
        assert result["human_interventions"] == 0

    def test_ignores_non_numeric_retries(self):
        state = {"retries": {"a": 3, "b": "bad", "c": None}}
        result = compute_retries(state)
        assert result["retries_total"] == 3

    def test_non_dict_retries_returns_zero(self):
        result = compute_retries({"retries": "bad_value"})
        assert result["retries_total"] == 0


# ---------------------------------------------------------------------------
# T-1c: compute_resolution — pass@k, regressions
# ---------------------------------------------------------------------------

class TestComputeResolution:
    def test_all_none_when_tasks_total_none(self):
        result = compute_resolution(None, None, 0, [], None)
        assert result["pass_at_1"] is None
        assert result["pass_at_2"] is None
        assert result["regressions"] is None
        assert result["regression_rate"] is None

    def test_all_none_when_tasks_total_zero(self):
        result = compute_resolution(0, 0, 0, [], None)
        assert result["pass_at_1"] is None

    def test_monotonic_pass_at_2_gte_pass_at_1(self):
        # 5 tasks, 4 completed, 2 retries
        result = compute_resolution(5, 4, 2, [], None)
        assert result["pass_at_2"] >= result["pass_at_1"]

    def test_pass_at_1_formula(self):
        # max(0, 4 - 1) / 4 = 0.75
        result = compute_resolution(4, 4, 1, [], None)
        assert abs(result["pass_at_1"] - 0.75) < 1e-6

    def test_pass_at_2_formula(self):
        # 3/4 = 0.75
        result = compute_resolution(4, 3, 0, [], None)
        assert abs(result["pass_at_2"] - 0.75) < 1e-6

    def test_counts_regression_entries(self):
        step_history = [
            {"step_id": "a", "regression": True},
            {"step_id": "b", "regression": False},
            {"step_id": "c", "regression": True},
        ]
        result = compute_resolution(3, 2, 0, step_history, None)
        assert result["regressions"] == 2

    def test_regression_rate(self):
        step_history = [{"regression": True}]
        result = compute_resolution(2, 2, 0, step_history, None)
        assert abs(result["regression_rate"] - 0.5) < 1e-6


# ---------------------------------------------------------------------------
# T-1d: run_git_churn — returns all-zeros default when subprocess fails
# ---------------------------------------------------------------------------

class TestRunGitChurn:
    def test_returns_zeros_on_nonexistent_worktree(self):
        result = run_git_churn("/nonexistent/path", "my-feature")
        assert result["files_changed"] == 0
        assert result["insertions"] == 0
        assert result["deletions"] == 0
        assert result["total_commits"] == 0
        assert result["rework_commits"] == 0
        assert result["rework_rate"] == 0.0

    def test_returns_zeros_when_no_commits(self, tmp_path):
        """Git process runs fine but returns no commits matching change_id."""
        import subprocess
        # Init a fresh repo with no commits matching the change_id
        subprocess.run(["git", "init", str(tmp_path)], capture_output=True)
        subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t.com"], capture_output=True)
        subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "T"], capture_output=True)
        result = run_git_churn(str(tmp_path), "xxxxxnocommits")
        assert result["total_commits"] == 0
        assert result["rework_rate"] == 0.0

    def test_returns_zeros_on_timeout(self):
        """When subprocess.run raises TimeoutExpired, return zeros."""
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 10)):
            result = run_git_churn("/some/path", "feature-id")
        assert result["total_commits"] == 0
        assert result["files_changed"] == 0


# ---------------------------------------------------------------------------
# T-1e: extract_review_scores — averages step_history[].review_score.overall
# ---------------------------------------------------------------------------

class TestExtractReviewScores:
    def test_averages_scores(self):
        state = {
            "step_history": [
                {"review_score": {"overall": 8}},
                {"review_score": {"overall": 9}},
            ]
        }
        result = extract_review_scores(state)
        assert result["avg"] == 8.5
        assert result["scores_list"] == [8.0, 9.0]

    def test_skips_non_numeric(self):
        state = {
            "step_history": [
                {"review_score": {"overall": "bad"}},
                {"review_score": {"overall": 7}},
            ]
        }
        result = extract_review_scores(state)
        assert result["scores_list"] == [7.0]
        assert result["avg"] == 7.0

    def test_no_scores_returns_none(self):
        state = {"step_history": []}
        result = extract_review_scores(state)
        assert result["avg"] is None
        assert result["scores_list"] == []

    def test_missing_review_score_skipped(self):
        state = {
            "step_history": [
                {"step_id": "x"},
                {"review_score": {"overall": 5}},
            ]
        }
        result = extract_review_scores(state)
        assert result["scores_list"] == [5.0]

    def test_absent_step_history(self):
        result = extract_review_scores({})
        assert result["avg"] is None


# ---------------------------------------------------------------------------
# T-1f: wall_clock_minutes — parses ISO timestamps, returns None when missing
# ---------------------------------------------------------------------------

class TestWallClockMinutes:
    def test_computes_minutes(self):
        state = {
            "started_at": "2026-01-01T00:00:00Z",
            "completed_at": "2026-01-01T01:00:00Z",
        }
        result = wall_clock_minutes(state)
        assert result == 60.0

    def test_returns_none_when_started_at_missing(self):
        result = wall_clock_minutes({"completed_at": "2026-01-01T00:00:00Z"})
        assert result is None

    def test_returns_none_when_completed_at_missing(self):
        result = wall_clock_minutes({"started_at": "2026-01-01T00:00:00Z"})
        assert result is None

    def test_returns_none_when_both_missing(self):
        result = wall_clock_minutes({})
        assert result is None

    def test_handles_datetime_objects(self):
        import datetime as dt
        state = {
            "started_at": dt.datetime(2026, 1, 1, 0, 0, 0, tzinfo=dt.timezone.utc),
            "completed_at": dt.datetime(2026, 1, 1, 0, 30, 0, tzinfo=dt.timezone.utc),
        }
        result = wall_clock_minutes(state)
        assert result == 30.0


# ---------------------------------------------------------------------------
# T-3: _resolve_feature_metrics — schema branching and dict shape
# ---------------------------------------------------------------------------

class TestResolveFeatureMetrics:
    """Imported from orchestrator_next.record — GREEN after T-4."""

    def _make_state(self, tmp_path: Path, schema="feature", with_tasks=True,
                    started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T01:00:00Z",
                    tasks_path_override=None):
        if with_tasks:
            tasks_md = tmp_path / "tasks.md"
            tasks_md.write_text("- [x] T1\n- [x] T2\n- [ ] T3\n")
        state = {
            "change_id": "my-feature",
            "schema": schema,
            "repo_root": str(tmp_path),
            "worktree_path": str(tmp_path),
            "started_at": started_at,
            "completed_at": completed_at,
            "step_history": [],
        }
        if tasks_path_override is not None:
            state["tasks_path"] = tasks_path_override
        elif with_tasks:
            state["tasks_path"] = str(tmp_path / "tasks.md")
        return state

    def test_feature_schema_returns_all_keys(self, tmp_path):
        from orchestrator_next.record import _resolve_feature_metrics
        state = self._make_state(tmp_path)
        result = _resolve_feature_metrics(state, "my-feature")

        expected_keys = {
            "schema_name", "tasks_total", "tasks_planned", "tasks_added",
            "tasks_completed", "tasks_failed", "resolve_rate",
            "pass_at_1", "pass_at_2", "regressions", "regression_rate",
            "retries_total", "human_interventions",
            "files_changed", "insertions", "deletions", "total_commits",
            "rework_commits", "rework_rate",
            "review_scores_json", "review_score_avg",
            "wall_clock_minutes", "source",
        }
        assert expected_keys == set(result.keys()), \
            f"missing: {expected_keys - set(result.keys())}, extra: {set(result.keys()) - expected_keys}"

    def test_spike_schema_returns_null_task_columns(self, tmp_path):
        from orchestrator_next.record import _resolve_feature_metrics
        state = self._make_state(tmp_path, schema="spike", with_tasks=False,
                                 started_at="2026-01-01T00:00:00Z",
                                 completed_at="2026-01-01T01:00:00Z")
        result = _resolve_feature_metrics(state, "my-feature")
        assert result["tasks_total"] is None
        assert result["tasks_planned"] is None
        assert result["tasks_completed"] is None
        assert result["tasks_failed"] is None
        assert result["resolve_rate"] is None

    def test_feature_schema_missing_tasks_md_raises(self, tmp_path):
        from orchestrator_next.record import _resolve_feature_metrics
        state = self._make_state(tmp_path, with_tasks=False,
                                 tasks_path_override=str(tmp_path / "nonexistent.md"))
        with pytest.raises(FileNotFoundError):
            _resolve_feature_metrics(state, "my-feature")

    def test_feature_schema_missing_started_at_raises(self, tmp_path):
        from orchestrator_next.record import _resolve_feature_metrics
        state = self._make_state(tmp_path, started_at=None)
        with pytest.raises(RuntimeError):
            _resolve_feature_metrics(state, "my-feature")

    def test_source_starts_with_done_at(self, tmp_path):
        from orchestrator_next.record import _resolve_feature_metrics
        state = self._make_state(tmp_path)
        result = _resolve_feature_metrics(state, "my-feature")
        assert result["source"].startswith("done@")

    def test_does_not_call_duckdb_connect(self, tmp_path):
        from orchestrator_next.record import _resolve_feature_metrics
        state = self._make_state(tmp_path)
        with patch("duckdb.connect", side_effect=RuntimeError("should not be called")):
            # Should NOT raise — duckdb.connect is not used
            result = _resolve_feature_metrics(state, "my-feature")
        assert result is not None

    def test_fallback_tasks_path_state_change_dir(self, tmp_path):
        """Falls back to <repo_root>/.state/<change_id>/tasks.md when tasks_path absent."""
        from orchestrator_next.record import _resolve_feature_metrics
        # Create the fallback path
        fallback = tmp_path / ".state" / "my-feature" / "tasks.md"
        fallback.parent.mkdir(parents=True)
        fallback.write_text("- [x] T1\n- [ ] T2\n")
        state = self._make_state(tmp_path, with_tasks=False, tasks_path_override=None)
        # Don't set tasks_path in state
        state.pop("tasks_path", None)
        state["repo_root"] = str(tmp_path)
        result = _resolve_feature_metrics(state, "my-feature")
        assert result["tasks_total"] == 2


# ---------------------------------------------------------------------------
# T-5: _write_feature_metrics — calls upsert_feature_metrics with right kwargs
# ---------------------------------------------------------------------------

class TestWriteFeatureMetrics:
    """Imported from orchestrator_next.record — GREEN after T-6."""

    def _minimal_data(self) -> dict:
        return {
            "schema_name": "feature",
            "tasks_total": 3,
            "tasks_planned": 3,
            "tasks_added": 0,
            "tasks_completed": 2,
            "tasks_failed": 1,
            "resolve_rate": 0.666667,
            "pass_at_1": 0.666667,
            "pass_at_2": 0.666667,
            "regressions": 0,
            "regression_rate": 0.0,
            "retries_total": 0,
            "human_interventions": 0,
            "files_changed": 5,
            "insertions": 100,
            "deletions": 50,
            "total_commits": 10,
            "rework_commits": 2,
            "rework_rate": 0.2,
            "review_scores_json": "[]",
            "review_score_avg": None,
            "wall_clock_minutes": 60.0,
            "source": "done@2026-01-01T00:00:00Z",
        }

    def test_writes_row_to_duckdb(self, tmp_path):
        from orchestrator_next.record import _write_feature_metrics
        db_path = tmp_path / "test.duckdb"
        db = duckdb.connect(str(db_path))
        ensure_schema(db)

        data = self._minimal_data()
        _write_feature_metrics(db, str(tmp_path), "my-feature", data)

        row = db.execute(
            "SELECT change_id, tasks_total FROM feature_metrics WHERE change_id = ?",
            ["my-feature"]
        ).fetchone()
        db.close()

        assert row is not None
        assert row[0] == "my-feature"
        assert row[1] == 3

    def test_helper_does_not_issue_begin_commit(self, tmp_path):
        from orchestrator_next.record import _write_feature_metrics
        db_path = tmp_path / "test.duckdb"
        db = duckdb.connect(str(db_path))
        ensure_schema(db)

        # Track calls to db.execute
        original_execute = db.execute
        calls = []
        def tracking_execute(sql, params=None):
            calls.append(sql.strip().upper()[:10])
            if params is not None:
                return original_execute(sql, params)
            return original_execute(sql)

        data = self._minimal_data()
        with patch.object(db, "execute", side_effect=tracking_execute):
            _write_feature_metrics(db, str(tmp_path), "my-feature", data)

        db.close()

        # Should not contain BEGIN or COMMIT — caller controls transaction
        assert "BEGIN" not in calls, f"_write_feature_metrics issued BEGIN: {calls}"
        assert "COMMIT" not in calls, f"_write_feature_metrics issued COMMIT: {calls}"

    def test_raises_when_upsert_raises(self, tmp_path):
        from orchestrator_next.record import _write_feature_metrics
        db_path = tmp_path / "test.duckdb"
        db = duckdb.connect(str(db_path))
        ensure_schema(db)

        with patch("orchestrator_next.upsert.upsert_feature_metrics",
                   side_effect=RuntimeError("simulated failure")):
            with pytest.raises(RuntimeError, match="simulated failure"):
                _write_feature_metrics(db, str(tmp_path), "my-feature", self._minimal_data())

        db.close()
