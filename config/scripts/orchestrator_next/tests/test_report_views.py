"""
T-1 (RED) / T-2 (GREEN): DuckDB view DDL shape + column coverage.

Tests cover FR-1, FR-2, FR-3, FR-4, FR-5, AC-1, AC-2, AC-3, AC-11.

Scenarios:
  (a) DESCRIBE feature_report returns the exact column list from FR-2 (52 cols).
  (b) One row per (repo_root, change_id) with seeded fixtures — AC-1.
  (c) COALESCE(SUM(cost_usd),0) handles NULL rows (UC-E1) — AC-2.
  (d) LEFT JOIN keeps changes whose feature_metrics row is missing (UC-E2) — AC-3.
  (e) Every zero-division guard fires correctly when denominator = 0 — AC-11.
  (f) per_agent_tokens / per_step are JSON strings; 2+ agents → 2+ top-level keys.
  (g) DESCRIBE assertions for phase_report, agent_report, repo_report.

Design: all tests use the real `ensure_schema(db)` (which auto-applies migrations)
so that T-2's migration auto-lands the views by writing 0002_report_views.sql and
T-1 fails with "View with name feature_report does not exist" in RED phase.
"""
from __future__ import annotations

import json
import os
import sys

import duckdb
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from orchestrator_next.upsert import ensure_schema  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def in_memory_db():
    db = duckdb.connect(":memory:")
    ensure_schema(db)
    yield db
    db.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _col_names(db, view: str) -> set[str]:
    """Return column names of a view as a set."""
    return {row[0] for row in db.execute(f"DESCRIBE {view}").fetchall()}


def _insert_step_event(
    db,
    *,
    repo_root: str = "/repo/root",
    change_id: str = "my-feature",
    phase: str = "implement",
    step_id: str = "execute-next-task",
    attempt: int = 1,
    agent_name: str = "developer",
    status: str = "completed",
    model: str = "claude-sonnet-4-6",
    input_tokens: int | None = 1000,
    output_tokens: int | None = 500,
    cache_creation_input_tokens: int | None = 200,
    cache_read_input_tokens: int | None = 100,
    cost_usd: float | None = 0.05,
    turns: int | None = 3,
    duration_ms: int | None = 5000,
) -> None:
    db.execute(
        """
        INSERT OR REPLACE INTO step_events (
          repo_root, change_id, phase, step_id, attempt,
          agent_name, status, model,
          input_tokens, output_tokens,
          cache_creation_input_tokens, cache_read_input_tokens,
          cost_usd, turns, duration_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            repo_root, change_id, phase, step_id, attempt,
            agent_name, status, model,
            input_tokens, output_tokens,
            cache_creation_input_tokens, cache_read_input_tokens,
            cost_usd, turns, duration_ms,
        ],
    )


def _insert_feature_metrics(
    db,
    *,
    repo_root: str = "/repo/root",
    change_id: str = "my-feature",
    tasks_total: int = 10,
    tasks_completed: int = 8,
    files_changed: int = 5,
    retries_total: int = 2,
    rework_commits: int = 1,
    rework_rate: float = 0.1,
    wall_clock_minutes: float = 30.0,
) -> None:
    db.execute(
        """
        INSERT OR REPLACE INTO feature_metrics (
          repo_root, change_id, schema_name,
          tasks_total, tasks_planned, tasks_added, tasks_completed, tasks_failed,
          resolve_rate, pass_at_1, pass_at_2, regressions, regression_rate,
          retries_total, human_interventions,
          files_changed, insertions, deletions, total_commits, rework_commits, rework_rate,
          review_scores_json, review_score_avg,
          wall_clock_minutes,
          source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            repo_root, change_id, "feature",
            tasks_total, tasks_total, 0, tasks_completed, 0,
            tasks_completed / tasks_total if tasks_total else 0.0,
            0.8, 0.9, 0, 0.0,
            retries_total, 0,
            files_changed, 20, 10, 5, rework_commits, rework_rate,
            "[]", 8.5,
            wall_clock_minutes,
            "test",
        ],
    )


# ---------------------------------------------------------------------------
# Expected column lists
# ---------------------------------------------------------------------------

FEATURE_REPORT_COLUMNS = {
    "repo_root", "change_id",
    "cost_usd", "input_tokens", "output_tokens",
    "cache_creation_input_tokens", "cache_read_input_tokens",
    "total_tokens", "turns", "duration_ms", "step_count",
    "rework_ratio", "model",
    "pricing_input_usd", "pricing_output_usd",
    "pricing_cache_read_usd", "pricing_cache_creation_usd",
    "gross_usd", "tool_calls_count",
    "category", "complexity",
    "tasks_total", "tasks_planned", "tasks_added",
    "tasks_completed", "tasks_failed",
    "resolve_rate", "pass_at_1", "pass_at_2",
    "regressions", "regression_rate",
    "retries_total", "human_interventions",
    "files_changed", "insertions", "deletions",
    "total_commits", "rework_commits", "rework_rate",
    "review_scores_json", "review_score_avg",
    "wall_clock_minutes",
    "cost_per_task_usd", "cost_per_resolution_usd",
    "tokens_per_task", "tokens_per_resolution",
    "input_output_ratio", "cache_hit_rate",
    "per_agent_tokens", "per_agent_tools",
    "per_tool_uses", "per_step",
}

PHASE_REPORT_COLUMNS = {
    "repo_root", "change_id", "phase",
    "cost_usd", "input_tokens", "output_tokens",
    "duration_ms", "step_count", "first_seen",
}

AGENT_REPORT_COLUMNS = {
    "repo_root", "change_id", "agent_name",
    "cost_usd", "input_tokens", "output_tokens",
    "duration_ms", "step_count",
}

REPO_REPORT_COLUMNS = {
    "repo_basename", "change_id",
    "cost_usd", "input_tokens", "output_tokens",
    "step_count", "first_seen",
}


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------


class TestFeatureReportDDL:
    """(a) DESCRIBE feature_report returns the exact column list from FR-2."""

    def test_feature_report_columns_exact(self, in_memory_db):
        cols = _col_names(in_memory_db, "feature_report")
        assert cols == FEATURE_REPORT_COLUMNS, (
            f"Column mismatch.\n"
            f"  Missing: {FEATURE_REPORT_COLUMNS - cols}\n"
            f"  Extra:   {cols - FEATURE_REPORT_COLUMNS}"
        )

    def test_feature_report_column_count(self, in_memory_db):
        cols = _col_names(in_memory_db, "feature_report")
        assert len(cols) == 52, f"Expected 52 columns, got {len(cols)}"

    def test_phase_report_columns_exact(self, in_memory_db):
        cols = _col_names(in_memory_db, "phase_report")
        assert cols == PHASE_REPORT_COLUMNS, (
            f"phase_report column mismatch.\n"
            f"  Missing: {PHASE_REPORT_COLUMNS - cols}\n"
            f"  Extra:   {cols - PHASE_REPORT_COLUMNS}"
        )

    def test_agent_report_columns_exact(self, in_memory_db):
        cols = _col_names(in_memory_db, "agent_report")
        assert cols == AGENT_REPORT_COLUMNS, (
            f"agent_report column mismatch.\n"
            f"  Missing: {AGENT_REPORT_COLUMNS - cols}\n"
            f"  Extra:   {cols - AGENT_REPORT_COLUMNS}"
        )

    def test_repo_report_columns_exact(self, in_memory_db):
        cols = _col_names(in_memory_db, "repo_report")
        assert cols == REPO_REPORT_COLUMNS, (
            f"repo_report column mismatch.\n"
            f"  Missing: {REPO_REPORT_COLUMNS - cols}\n"
            f"  Extra:   {cols - REPO_REPORT_COLUMNS}"
        )


class TestFeatureReportOneRowPerChange:
    """(b) One row per (repo_root, change_id) — AC-1."""

    def test_single_change_produces_one_row(self, in_memory_db):
        db = in_memory_db
        _insert_step_event(db, step_id="T-1")
        _insert_step_event(db, step_id="T-2")
        rows = db.execute(
            "SELECT COUNT(*) FROM feature_report WHERE change_id = 'my-feature'"
        ).fetchone()
        assert rows[0] == 1, "feature_report must produce exactly one row per change_id"

    def test_two_changes_produce_two_rows(self, in_memory_db):
        db = in_memory_db
        _insert_step_event(db, change_id="feature-a", step_id="T-1")
        _insert_step_event(db, change_id="feature-b", step_id="T-1")
        rows = db.execute("SELECT COUNT(*) FROM feature_report").fetchone()
        assert rows[0] == 2, "Two distinct change_ids must produce two rows in feature_report"

    def test_totals_aggregated_correctly(self, in_memory_db):
        db = in_memory_db
        _insert_step_event(db, step_id="T-1", cost_usd=0.05, input_tokens=1000, output_tokens=500)
        _insert_step_event(db, step_id="T-2", cost_usd=0.10, input_tokens=2000, output_tokens=800)
        row = db.execute(
            "SELECT cost_usd, input_tokens, output_tokens, step_count FROM feature_report"
        ).fetchone()
        assert abs(row[0] - 0.15) < 1e-9, f"cost_usd should be 0.15, got {row[0]}"
        assert row[1] == 3000, f"input_tokens should be 3000, got {row[1]}"
        assert row[2] == 1300, f"output_tokens should be 1300, got {row[2]}"
        assert row[3] == 2, f"step_count should be 2, got {row[3]}"


class TestNullCostUsd:
    """(c) COALESCE(SUM(cost_usd),0) handles NULL (UC-E1) — AC-2."""

    def test_null_cost_usd_excluded_from_sum(self, in_memory_db):
        db = in_memory_db
        # One completed row with cost_usd=0.05, one in-progress row with NULL cost_usd
        _insert_step_event(
            db, step_id="T-1", cost_usd=0.05, status="completed"
        )
        _insert_step_event(
            db, step_id="T-2", cost_usd=None, status="in_progress",
            attempt=1, input_tokens=None, output_tokens=None,
            cache_creation_input_tokens=None, cache_read_input_tokens=None,
            turns=None, duration_ms=None
        )
        row = db.execute(
            "SELECT cost_usd FROM feature_report WHERE change_id = 'my-feature'"
        ).fetchone()
        assert row is not None, "feature_report must return a row even with NULL cost_usd rows"
        assert abs(row[0] - 0.05) < 1e-9, (
            f"cost_usd should equal sum of non-NULL rows (0.05), got {row[0]}"
        )

    def test_all_null_costs_return_zero(self, in_memory_db):
        db = in_memory_db
        _insert_step_event(
            db, step_id="T-1", cost_usd=None, status="in_progress",
            input_tokens=None, output_tokens=None,
            cache_creation_input_tokens=None, cache_read_input_tokens=None,
            turns=None, duration_ms=None
        )
        row = db.execute(
            "SELECT cost_usd FROM feature_report WHERE change_id = 'my-feature'"
        ).fetchone()
        assert row is not None
        assert row[0] == 0.0, f"All-NULL cost_usd must COALESCE to 0.0, got {row[0]}"


class TestMissingFeatureMetrics:
    """(d) LEFT JOIN keeps changes whose feature_metrics row is missing (UC-E2) — AC-3."""

    def test_missing_feature_metrics_returns_row(self, in_memory_db):
        db = in_memory_db
        # Insert step_events but NO feature_metrics row
        _insert_step_event(db, change_id="no-metrics-feature", step_id="T-1")
        rows = db.execute(
            "SELECT * FROM feature_report WHERE change_id = 'no-metrics-feature'"
        ).fetchall()
        assert len(rows) == 1, "Missing feature_metrics must not drop the feature row"

    def test_missing_feature_metrics_has_null_resolution_columns(self, in_memory_db):
        db = in_memory_db
        _insert_step_event(db, change_id="no-metrics-feature", step_id="T-1")
        row = db.execute(
            """SELECT resolve_rate, files_changed, wall_clock_minutes, retries_total
               FROM feature_report WHERE change_id = 'no-metrics-feature'"""
        ).fetchone()
        assert row is not None
        assert row[0] is None, "resolve_rate must be NULL when feature_metrics is absent"
        assert row[1] is None, "files_changed must be NULL when feature_metrics is absent"
        assert row[2] is None, "wall_clock_minutes must be NULL when feature_metrics is absent"
        assert row[3] is None, "retries_total must be NULL when feature_metrics is absent"

    def test_missing_feature_metrics_still_has_cost_tokens(self, in_memory_db):
        db = in_memory_db
        _insert_step_event(
            db, change_id="no-metrics-feature", step_id="T-1",
            cost_usd=0.07, input_tokens=500, output_tokens=200
        )
        row = db.execute(
            "SELECT cost_usd, input_tokens, output_tokens FROM feature_report WHERE change_id = 'no-metrics-feature'"
        ).fetchone()
        assert row is not None
        assert abs(row[0] - 0.07) < 1e-9, f"cost_usd should be 0.07, got {row[0]}"
        assert row[1] == 500, f"input_tokens should be 500, got {row[1]}"
        assert row[2] == 200, f"output_tokens should be 200, got {row[2]}"


class TestZeroDivisionGuards:
    """(e) Every zero-division guard fires when denominator = 0 — AC-11."""

    def _seed_feature(self, db: duckdb.DuckDBPyConnection) -> None:
        """Insert a single step_event row with zero tokens / cost."""
        _insert_step_event(
            db, step_id="T-1",
            input_tokens=0, output_tokens=0,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
            cost_usd=0.0, turns=0, duration_ms=0,
        )

    def test_rework_ratio_zero_when_no_cost(self, in_memory_db):
        db = in_memory_db
        self._seed_feature(db)
        row = db.execute(
            "SELECT rework_ratio FROM feature_report WHERE change_id = 'my-feature'"
        ).fetchone()
        assert row[0] == 0.0, f"rework_ratio should be 0.0 when rework_denom=0, got {row[0]}"

    def test_cost_per_task_usd_zero_when_tasks_total_zero(self, in_memory_db):
        db = in_memory_db
        self._seed_feature(db)
        # feature_metrics with tasks_total=0
        db.execute(
            """INSERT OR REPLACE INTO feature_metrics
               (repo_root, change_id, schema_name,
                tasks_total, tasks_planned, tasks_added, tasks_completed, tasks_failed,
                resolve_rate, pass_at_1, pass_at_2, regressions, regression_rate,
                retries_total, human_interventions,
                files_changed, insertions, deletions, total_commits, rework_commits, rework_rate,
                review_scores_json, review_score_avg, wall_clock_minutes, source)
               VALUES (?,?,?,0,0,0,0,0, 0,0,0,0,0, 0,0, 0,0,0,0,0,0, '[]',0,0,'test')""",
            ["/repo/root", "my-feature", "feature"],
        )
        row = db.execute(
            "SELECT cost_per_task_usd FROM feature_report WHERE change_id = 'my-feature'"
        ).fetchone()
        assert row[0] == 0.0, f"cost_per_task_usd should be 0.0 when tasks_total=0, got {row[0]}"

    def test_cost_per_resolution_usd_zero_when_tasks_completed_zero(self, in_memory_db):
        db = in_memory_db
        self._seed_feature(db)
        db.execute(
            """INSERT OR REPLACE INTO feature_metrics
               (repo_root, change_id, schema_name,
                tasks_total, tasks_planned, tasks_added, tasks_completed, tasks_failed,
                resolve_rate, pass_at_1, pass_at_2, regressions, regression_rate,
                retries_total, human_interventions,
                files_changed, insertions, deletions, total_commits, rework_commits, rework_rate,
                review_scores_json, review_score_avg, wall_clock_minutes, source)
               VALUES (?,?,?,5,5,0,0,0, 0,0,0,0,0, 0,0, 0,0,0,0,0,0, '[]',0,0,'test')""",
            ["/repo/root", "my-feature", "feature"],
        )
        row = db.execute(
            "SELECT cost_per_resolution_usd FROM feature_report WHERE change_id = 'my-feature'"
        ).fetchone()
        assert row[0] == 0.0, (
            f"cost_per_resolution_usd should be 0.0 when tasks_completed=0, got {row[0]}"
        )

    def test_tokens_per_task_zero_when_tasks_total_zero(self, in_memory_db):
        db = in_memory_db
        self._seed_feature(db)
        db.execute(
            """INSERT OR REPLACE INTO feature_metrics
               (repo_root, change_id, schema_name,
                tasks_total, tasks_planned, tasks_added, tasks_completed, tasks_failed,
                resolve_rate, pass_at_1, pass_at_2, regressions, regression_rate,
                retries_total, human_interventions,
                files_changed, insertions, deletions, total_commits, rework_commits, rework_rate,
                review_scores_json, review_score_avg, wall_clock_minutes, source)
               VALUES (?,?,?,0,0,0,0,0, 0,0,0,0,0, 0,0, 0,0,0,0,0,0, '[]',0,0,'test')""",
            ["/repo/root", "my-feature", "feature"],
        )
        row = db.execute(
            "SELECT tokens_per_task FROM feature_report WHERE change_id = 'my-feature'"
        ).fetchone()
        assert row[0] == 0, f"tokens_per_task should be 0 when tasks_total=0, got {row[0]}"

    def test_tokens_per_resolution_zero_when_tasks_completed_zero(self, in_memory_db):
        db = in_memory_db
        self._seed_feature(db)
        db.execute(
            """INSERT OR REPLACE INTO feature_metrics
               (repo_root, change_id, schema_name,
                tasks_total, tasks_planned, tasks_added, tasks_completed, tasks_failed,
                resolve_rate, pass_at_1, pass_at_2, regressions, regression_rate,
                retries_total, human_interventions,
                files_changed, insertions, deletions, total_commits, rework_commits, rework_rate,
                review_scores_json, review_score_avg, wall_clock_minutes, source)
               VALUES (?,?,?,5,5,0,0,0, 0,0,0,0,0, 0,0, 0,0,0,0,0,0, '[]',0,0,'test')""",
            ["/repo/root", "my-feature", "feature"],
        )
        row = db.execute(
            "SELECT tokens_per_resolution FROM feature_report WHERE change_id = 'my-feature'"
        ).fetchone()
        assert row[0] == 0, (
            f"tokens_per_resolution should be 0 when tasks_completed=0, got {row[0]}"
        )

    def test_input_output_ratio_zero_when_output_tokens_zero(self, in_memory_db):
        db = in_memory_db
        self._seed_feature(db)
        row = db.execute(
            "SELECT input_output_ratio FROM feature_report WHERE change_id = 'my-feature'"
        ).fetchone()
        assert row[0] == 0.0, (
            f"input_output_ratio should be 0.0 when output_tokens=0, got {row[0]}"
        )

    def test_cache_hit_rate_zero_when_total_tokens_zero(self, in_memory_db):
        db = in_memory_db
        self._seed_feature(db)
        row = db.execute(
            "SELECT cache_hit_rate FROM feature_report WHERE change_id = 'my-feature'"
        ).fetchone()
        assert row[0] == 0.0, (
            f"cache_hit_rate should be 0.0 when token sum=0, got {row[0]}"
        )


class TestPerAgentTokensJSON:
    """(f) per_agent_tokens / per_step are JSON strings; 2+ agents → 2+ top-level keys."""

    def test_per_agent_tokens_is_valid_json(self, in_memory_db):
        db = in_memory_db
        _insert_step_event(db, agent_name="developer", step_id="T-1")
        row = db.execute(
            "SELECT per_agent_tokens FROM feature_report WHERE change_id = 'my-feature'"
        ).fetchone()
        assert row is not None
        parsed = json.loads(row[0])
        assert isinstance(parsed, dict), f"per_agent_tokens must be a JSON object, got {type(parsed)}"

    def test_per_agent_tokens_two_agents_two_keys(self, in_memory_db):
        """Two distinct agents must produce two top-level keys, not one row per agent."""
        db = in_memory_db
        _insert_step_event(db, agent_name="developer", step_id="T-1", attempt=1)
        _insert_step_event(db, agent_name="reviewer", step_id="T-1", attempt=2)
        rows = db.execute(
            "SELECT per_agent_tokens FROM feature_report WHERE change_id = 'my-feature'"
        ).fetchall()
        # Must be exactly ONE row in feature_report (not one per agent)
        assert len(rows) == 1, f"feature_report must return exactly 1 row, got {len(rows)}"
        parsed = json.loads(rows[0][0])
        assert len(parsed) >= 2, (
            f"per_agent_tokens must have at least 2 keys for 2 agents, got {list(parsed.keys())}"
        )
        assert "developer" in parsed, "per_agent_tokens must have 'developer' key"
        assert "reviewer" in parsed, "per_agent_tokens must have 'reviewer' key"

    def test_per_agent_tokens_agent_struct_fields(self, in_memory_db):
        """Each agent value must contain the expected fields."""
        db = in_memory_db
        _insert_step_event(
            db, agent_name="developer", step_id="T-1",
            input_tokens=1000, output_tokens=500, cost_usd=0.05
        )
        row = db.execute(
            "SELECT per_agent_tokens FROM feature_report WHERE change_id = 'my-feature'"
        ).fetchone()
        parsed = json.loads(row[0])
        dev = parsed["developer"]
        for field in ("total_tokens", "input_tokens", "output_tokens", "cost_usd", "duration_ms", "step_count"):
            assert field in dev, f"per_agent_tokens.developer must contain '{field}'"
        assert dev["input_tokens"] == 1000
        assert dev["output_tokens"] == 500
        assert dev["total_tokens"] == 1500

    def test_per_step_is_valid_json(self, in_memory_db):
        db = in_memory_db
        _insert_step_event(db, step_id="execute-next-task")
        row = db.execute(
            "SELECT per_step FROM feature_report WHERE change_id = 'my-feature'"
        ).fetchone()
        assert row is not None
        parsed = json.loads(row[0])
        assert isinstance(parsed, dict), f"per_step must be a JSON object, got {type(parsed)}"

    def test_per_step_contains_expected_step_id(self, in_memory_db):
        db = in_memory_db
        _insert_step_event(db, step_id="execute-next-task")
        row = db.execute(
            "SELECT per_step FROM feature_report WHERE change_id = 'my-feature'"
        ).fetchone()
        parsed = json.loads(row[0])
        assert "execute-next-task" in parsed, (
            f"per_step must have step_id key 'execute-next-task', got {list(parsed.keys())}"
        )
        step = parsed["execute-next-task"]
        for field in ("total_tokens", "tool_uses", "duration_ms", "executions"):
            assert field in step, f"per_step entry must contain '{field}'"


class TestPhaseReportShape:
    """phase_report column coverage and basic aggregation."""

    def test_phase_report_one_row_per_phase(self, in_memory_db):
        db = in_memory_db
        _insert_step_event(db, phase="plan", step_id="T-1")
        _insert_step_event(db, phase="implement", step_id="T-2")
        rows = db.execute(
            "SELECT phase FROM phase_report WHERE change_id = 'my-feature' ORDER BY phase"
        ).fetchall()
        phases = [r[0] for r in rows]
        assert "implement" in phases, "phase_report must have an 'implement' row"
        assert "plan" in phases, "phase_report must have a 'plan' row"
        assert len(phases) == 2, f"Expected 2 phase rows, got {phases}"

    def test_phase_report_aggregates_cost(self, in_memory_db):
        db = in_memory_db
        _insert_step_event(db, phase="implement", step_id="T-1", cost_usd=0.03)
        _insert_step_event(db, phase="implement", step_id="T-2", cost_usd=0.07)
        row = db.execute(
            "SELECT cost_usd FROM phase_report WHERE change_id = 'my-feature' AND phase = 'implement'"
        ).fetchone()
        assert abs(row[0] - 0.10) < 1e-9, f"phase_report cost_usd should be 0.10, got {row[0]}"


class TestAgentReportShape:
    """agent_report column coverage and basic aggregation."""

    def test_agent_report_one_row_per_agent(self, in_memory_db):
        db = in_memory_db
        _insert_step_event(db, agent_name="developer", step_id="T-1")
        _insert_step_event(db, agent_name="reviewer", step_id="T-2")
        rows = db.execute(
            "SELECT agent_name FROM agent_report WHERE change_id = 'my-feature'"
        ).fetchall()
        agents = {r[0] for r in rows}
        assert "developer" in agents
        assert "reviewer" in agents
        assert len(agents) == 2

    def test_agent_report_ordered_by_agent_name(self, in_memory_db):
        db = in_memory_db
        _insert_step_event(db, agent_name="zebra-agent", step_id="T-1")
        _insert_step_event(db, agent_name="alpha-agent", step_id="T-2")
        rows = db.execute(
            "SELECT agent_name FROM agent_report WHERE change_id = 'my-feature'"
        ).fetchall()
        names = [r[0] for r in rows]
        assert names == sorted(names), f"agent_report must be ordered by agent_name, got {names}"


class TestRepoReportShape:
    """repo_report column coverage and repo_basename extraction."""

    def test_repo_report_extracts_basename(self, in_memory_db):
        db = in_memory_db
        _insert_step_event(db, repo_root="/Users/spidey/code/orchestrator", step_id="T-1")
        row = db.execute(
            "SELECT repo_basename FROM repo_report"
        ).fetchone()
        assert row is not None
        assert row[0] == "orchestrator", f"repo_basename should be 'orchestrator', got {row[0]}"

    def test_repo_report_aggregates_step_count(self, in_memory_db):
        db = in_memory_db
        _insert_step_event(db, step_id="T-1")
        _insert_step_event(db, step_id="T-2")
        row = db.execute(
            "SELECT step_count FROM repo_report WHERE change_id = 'my-feature'"
        ).fetchone()
        assert row[0] == 2, f"repo_report step_count should be 2, got {row[0]}"
