"""Tests for config/steps/compute-swe-metrics/compute_swe_metrics.py.

Covers:
  (a) _rows_to_metrics maps DuckDB row dict to correct YAML output shape.
  (b) compute_swe_metrics.py main() runs against a live in-memory DB via
      the current schema (ensure_schema) and emits valid metrics YAML.
  (c) COMPUTE_SWE_SOURCE_TS override makes output deterministic.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import duckdb
import pytest
import yaml

_HERE = Path(__file__).parent
_REPO = _HERE.parent.parent
_SCRIPT = _REPO / "config" / "steps" / "compute-swe-metrics" / "compute_swe_metrics.py"

# Load the module under test directly (not installed as a package).
_spec = importlib.util.spec_from_file_location("compute_swe_metrics", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

_rows_to_metrics = _mod._rows_to_metrics

from orchestrator_next.upsert import ensure_schema  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_step(
    db,
    *,
    change_id: str = "my-feature",
    step_id: str = "T-1",
    agent_name: str | None = "developer",
    input_tokens: int = 1000,
    output_tokens: int = 500,
    cost_usd: float = 0.05,
    duration_ms: int = 5000,
    model: str = "claude-sonnet-4-6",
    attempt: int = 1,
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
            "/repo", change_id, "implement", step_id, attempt,
            agent_name, "completed", model,
            input_tokens, output_tokens, 0, 0,
            cost_usd, 1, duration_ms,
        ],
    )


def _live_db() -> duckdb.DuckDBPyConnection:
    db = duckdb.connect(":memory:")
    ensure_schema(db)
    return db


# ---------------------------------------------------------------------------
# (a) _rows_to_metrics unit tests
# ---------------------------------------------------------------------------

def _minimal_row(**overrides) -> dict:
    base = {
        "input_tokens": 1000,
        "output_tokens": 500,
        "cache_creation_input_tokens": 200,
        "cache_read_input_tokens": 100,
        "total_tokens": 1800,
        "cost_usd": 0.05,
        "gross_usd": 0.06,
        "model": "claude-sonnet-4-6",
        "pricing_input_usd": 3.0,
        "pricing_output_usd": 15.0,
        "pricing_cache_read_usd": 0.3,
        "pricing_cache_creation_usd": 3.75,
        "turns": 3,
        "tool_calls_count": 10,
        "wall_clock_minutes": 5.0,
        "category": "feature",
        "human_interventions": 1,
        "rework_commits": 0,
        "rework_rate": 0.0,
        "tasks_total": 5,
        "tasks_planned": 4,
        "tasks_added": 1,
        "tasks_completed": 5,
        "tasks_failed": 0,
        "resolve_rate": 1.0,
        "pass_at_1": 0.8,
        "pass_at_2": 0.9,
        "regressions": 0,
        "regression_rate": 0.0,
        "retries_total": 1,
        "files_changed": 10,
        "insertions": 200,
        "deletions": 50,
        "total_commits": 5,
        "review_scores_json": '[{"score": 9}, {"score": 8}]',
        "review_score_avg": 8.5,
        "per_agent_tokens": '{"developer": {"total_tokens": 1800}}',
        "per_agent_tools": '{"developer": {"Bash": 5}}',
        "per_tool_uses": '{"Bash": 5}',
        "per_step": '{"T-1": {"total_tokens": 1800, "executions": 1}}',
        "cost_per_task_usd": 0.01,
        "cost_per_resolution_usd": 0.01,
        "tokens_per_task": 360,
        "tokens_per_resolution": 360,
        "input_output_ratio": 2.0,
        "cache_hit_rate": 0.05,
    }
    base.update(overrides)
    return base


class TestRowsToMetrics:
    def test_basic_shape(self):
        m = _rows_to_metrics([_minimal_row()], "BASELINE")
        assert m["tokens"]["input"] == 1000
        assert m["tokens"]["output"] == 500
        assert m["tokens"]["total"] == 1800
        assert m["cost"]["net_usd"] == 0.05
        assert m["source"] == "duckdb@BASELINE"

    def test_bigint_strings_cast_to_int(self):
        row = _minimal_row(input_tokens="42000", output_tokens="8000", total_tokens="50000", turns="7")
        m = _rows_to_metrics([row], "ts")
        assert m["tokens"]["input"] == 42000
        assert m["tokens"]["output"] == 8000
        assert m["turns"] == 7

    def test_none_values_become_zero(self):
        row = _minimal_row(input_tokens=None, output_tokens=None, total_tokens=None)
        m = _rows_to_metrics([row], "ts")
        assert m["tokens"]["input"] == 0
        assert m["tokens"]["output"] == 0

    def test_review_scores_parsed(self):
        m = _rows_to_metrics([_minimal_row()], "ts")
        assert m["review_scores"] == [{"score": 9}, {"score": 8}]

    def test_review_scores_empty_when_null(self):
        m = _rows_to_metrics([_minimal_row(review_scores_json=None)], "ts")
        assert m["review_scores"] == []

    def test_review_scores_empty_on_bad_json(self):
        m = _rows_to_metrics([_minimal_row(review_scores_json="not json")], "ts")
        assert m["review_scores"] == []

    def test_per_step_is_dict(self):
        m = _rows_to_metrics([_minimal_row()], "ts")
        assert isinstance(m["per_step"], dict)
        assert "T-1" in m["per_step"]

    def test_per_agent_tokens_sorted_keys(self):
        row = _minimal_row(per_agent_tokens='{"z": {}, "a": {}}')
        m = _rows_to_metrics([row], "ts")
        assert m["per_agent_tokens"] == '{"a": {}, "z": {}}'

    def test_empty_rows_raises(self):
        with pytest.raises(ValueError, match="no events"):
            _rows_to_metrics([], "ts")

    def test_lint_delta_always_zero(self):
        m = _rows_to_metrics([_minimal_row()], "ts")
        assert m["lint_delta"] == 0


# ---------------------------------------------------------------------------
# (b) End-to-end: main() against live in-memory DB
# ---------------------------------------------------------------------------

class TestMainEndToEnd:
    def test_emits_valid_metrics_yaml(self, tmp_path):
        db_path = tmp_path / "test.duckdb"
        db = duckdb.connect(str(db_path))
        ensure_schema(db)
        _seed_step(db, change_id="test-e2e", cost_usd=0.15)
        db.close()

        state = tmp_path / "state.yaml"
        state.write_text("change_id: test-e2e\n")

        env = {
            **os.environ,
            "STATE_YAML_PATH": str(state),
            "CHANGE_ID": "test-e2e",
            "METRICS_DB": str(db_path),
            "COMPUTE_SWE_SOURCE_TS": "BASELINE",
        }
        result = subprocess.run(
            [sys.executable, str(_SCRIPT)],
            capture_output=True, text=True, env=env,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        parsed = yaml.safe_load(result.stdout)
        assert "metrics" in parsed
        assert parsed["metrics"]["source"] == "duckdb@BASELINE"

    def test_missing_change_id_exits_1(self, tmp_path):
        db_path = tmp_path / "test.duckdb"
        db = duckdb.connect(str(db_path))
        ensure_schema(db)
        db.close()

        state = tmp_path / "state.yaml"
        state.write_text("change_id: nonexistent\n")

        env = {
            **os.environ,
            "STATE_YAML_PATH": str(state),
            "CHANGE_ID": "nonexistent",
            "METRICS_DB": str(db_path),
        }
        result = subprocess.run(
            [sys.executable, str(_SCRIPT)],
            capture_output=True, text=True, env=env,
        )
        assert result.returncode == 1
        assert "no events" in result.stderr

    def test_slug_guard_rejects_bad_change_id(self, tmp_path):
        db_path = tmp_path / "test.duckdb"
        db = duckdb.connect(str(db_path))
        ensure_schema(db)
        db.close()

        state = tmp_path / "state.yaml"
        state.write_text("change_id: bad id\n")

        env = {
            **os.environ,
            "STATE_YAML_PATH": str(state),
            "CHANGE_ID": "bad id; DROP TABLE step_events;",
            "METRICS_DB": str(db_path),
        }
        result = subprocess.run(
            [sys.executable, str(_SCRIPT)],
            capture_output=True, text=True, env=env,
        )
        assert result.returncode == 3

    def test_deterministic_output(self, tmp_path):
        """Two runs with frozen timestamp produce byte-identical output."""
        db_path = tmp_path / "test.duckdb"
        db = duckdb.connect(str(db_path))
        ensure_schema(db)
        _seed_step(db, change_id="det-test")
        db.close()

        state = tmp_path / "state.yaml"
        state.write_text("change_id: det-test\n")

        env = {
            **os.environ,
            "STATE_YAML_PATH": str(state),
            "CHANGE_ID": "det-test",
            "METRICS_DB": str(db_path),
            "COMPUTE_SWE_SOURCE_TS": "FIXED",
        }
        r1 = subprocess.run([sys.executable, str(_SCRIPT)], capture_output=True, text=True, env=env)
        r2 = subprocess.run([sys.executable, str(_SCRIPT)], capture_output=True, text=True, env=env)
        assert r1.returncode == 0
        assert r1.stdout == r2.stdout

    def test_null_agent_script_step_excluded_from_per_agent_tokens(self, tmp_path):
        """Script steps (agent_name=NULL after ORC-77) must not appear in per_agent_tokens."""
        db_path = tmp_path / "test.duckdb"
        db = duckdb.connect(str(db_path))
        ensure_schema(db)
        _seed_step(db, change_id="agent-test", step_id="T-1", agent_name="developer")
        _seed_step(db, change_id="agent-test", step_id="script-step", agent_name=None, cost_usd=0.0)
        db.close()

        state = tmp_path / "state.yaml"
        state.write_text("change_id: agent-test\n")

        env = {
            **os.environ,
            "STATE_YAML_PATH": str(state),
            "CHANGE_ID": "agent-test",
            "METRICS_DB": str(db_path),
            "COMPUTE_SWE_SOURCE_TS": "BASELINE",
        }
        result = subprocess.run([sys.executable, str(_SCRIPT)], capture_output=True, text=True, env=env)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        parsed = yaml.safe_load(result.stdout)
        per_agent = json.loads(parsed["metrics"]["per_agent_tokens"])
        assert "developer" in per_agent
        assert None not in per_agent
        assert "null" not in per_agent
