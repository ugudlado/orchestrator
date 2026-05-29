"""
Tests for cost_report.py — "Tool not in step allowlist" anomaly subsection.

T-5: RED tests — verify step-allowlist anomaly detection.
These tests fail until T-6 adds _step_allowlist_anomalies and updates render.

Tests:
- tool_calls row for tool not in step's allowed_tools -> flagged in anomalies_step_allowlist
- tool_calls row for tool in allowed_tools -> NOT flagged (no false positives)
- step contract with empty allowed_tools -> no entry regardless of tool (no false positives)
- step contract missing at report time -> silently skipped (no crash)
- rendered markdown has "Tool not in step allowlist" subsection only when non-empty
- existing "Tool not in role" subsection unaffected
"""
from __future__ import annotations

import os
import sys

import pytest
import yaml
import duckdb

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def steps_dir(tmp_path):
    d = tmp_path / "steps"
    d.mkdir()
    return d


@pytest.fixture(autouse=True)
def set_override(steps_dir, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(steps_dir))


def _write_contract(steps_dir, step_id: str, data: dict):
    (steps_dir / f"{step_id}.yaml").write_text(yaml.dump(data))


@pytest.fixture()
def db():
    """Create an in-memory DuckDB with the tool_calls table."""
    conn = duckdb.connect(":memory:")
    conn.execute("""
        CREATE TABLE tool_calls (
            repo_root   TEXT NOT NULL,
            change_id   TEXT NOT NULL,
            phase       TEXT NOT NULL,
            step_id     TEXT NOT NULL,
            attempt     INTEGER NOT NULL,
            agent_name  TEXT NOT NULL,
            tool_name   TEXT NOT NULL,
            is_mcp      BOOLEAN NOT NULL,
            call_seq    INTEGER NOT NULL,
            called_at   TEXT,
            duration_ms BIGINT,
            PRIMARY KEY (repo_root, change_id, phase, step_id, attempt, call_seq)
        )
    """)
    conn.execute("""
        CREATE TABLE step_events (
            repo_root  TEXT NOT NULL,
            change_id  TEXT NOT NULL,
            phase      TEXT NOT NULL,
            step_id    TEXT NOT NULL,
            attempt    INTEGER NOT NULL,
            agent_name TEXT NOT NULL,
            status     TEXT,
            started_at TEXT,
            cost_usd DOUBLE,
            input_tokens BIGINT,
            output_tokens BIGINT,
            model TEXT,
            duration_ms INTEGER,
            PRIMARY KEY (repo_root, change_id, phase, step_id, attempt, status)
        )
    """)
    return conn


def _insert_tool_call(conn, repo_root: str, change_id: str, phase: str,
                      step_id: str, agent_name: str, tool_name: str, calls: int = 1):
    """Insert tool_calls rows for testing."""
    for i in range(calls):
        conn.execute("""
            INSERT INTO tool_calls
              (repo_root, change_id, phase, step_id, attempt, agent_name,
               tool_name, is_mcp, call_seq)
            VALUES (?, ?, ?, ?, 1, ?, ?, false, ?)
        """, [repo_root, change_id, phase, step_id, agent_name, tool_name, i + 1])


# ---------------------------------------------------------------------------
# T-5: _step_allowlist_anomalies function existence
# ---------------------------------------------------------------------------

class TestStepAllowlistAnomaliesFunction:

    def test_function_exists(self):
        """_step_allowlist_anomalies function must exist in cost_report module."""
        from orchestrator_next import cost_report
        assert hasattr(cost_report, "_step_allowlist_anomalies"), (
            "_step_allowlist_anomalies not found in cost_report"
        )


# ---------------------------------------------------------------------------
# T-5: anomaly detection logic
# ---------------------------------------------------------------------------

class TestStepAllowlistAnomalyDetection:

    def test_tool_not_in_allowlist_flagged(self, db, steps_dir):
        """tool_calls row with tool not in step's allowed_tools -> flagged."""
        _write_contract(steps_dir, "my-step", {
            "id": "my-step", "agent": "developer",
            "instruction": "do thing", "inputs": [], "outputs": [],
            "allowed_tools": ["Read", "Grep", "Glob"],
        })
        _insert_tool_call(db, "/repo", "test-change", "implement", "my-step",
                          "developer", "WebSearch", calls=2)

        from orchestrator_next.cost_report import _step_allowlist_anomalies
        result = _step_allowlist_anomalies(db, "/repo", "test-change")

        assert len(result) == 1
        row = result[0]
        assert row["agent_name"] == "developer"
        assert row["tool_name"] == "WebSearch"
        assert row["step_id"] == "my-step"
        assert row["calls"] == 2

    def test_tool_in_allowlist_not_flagged(self, db, steps_dir):
        """tool_calls row with tool in allowed_tools -> NOT flagged (no false positive)."""
        _write_contract(steps_dir, "my-step", {
            "id": "my-step", "agent": "developer",
            "instruction": "do thing", "inputs": [], "outputs": [],
            "allowed_tools": ["Read", "Grep"],
        })
        _insert_tool_call(db, "/repo", "test-change", "implement", "my-step",
                          "developer", "Read")

        from orchestrator_next.cost_report import _step_allowlist_anomalies
        result = _step_allowlist_anomalies(db, "/repo", "test-change")
        assert result == []

    def test_empty_allowed_tools_not_flagged(self, db, steps_dir):
        """Step with empty allowed_tools -> no entry, even for exotic tool (no false positive)."""
        _write_contract(steps_dir, "my-step", {
            "id": "my-step", "agent": "developer",
            "instruction": "do thing", "inputs": [], "outputs": [],
            "allowed_tools": [],
        })
        _insert_tool_call(db, "/repo", "test-change", "implement", "my-step",
                          "developer", "WebSearch")

        from orchestrator_next.cost_report import _step_allowlist_anomalies
        result = _step_allowlist_anomalies(db, "/repo", "test-change")
        assert result == []

    def test_absent_allowed_tools_not_flagged(self, db, steps_dir):
        """Step without allowed_tools field -> no entry (no restriction)."""
        _write_contract(steps_dir, "my-step", {
            "id": "my-step", "agent": "developer",
            "instruction": "do thing", "inputs": [], "outputs": [],
        })
        _insert_tool_call(db, "/repo", "test-change", "implement", "my-step",
                          "developer", "WebSearch")

        from orchestrator_next.cost_report import _step_allowlist_anomalies
        result = _step_allowlist_anomalies(db, "/repo", "test-change")
        assert result == []

    def test_missing_contract_skipped_silently(self, db, steps_dir):
        """Missing step contract at report time -> silently skipped (no crash)."""
        # NO contract written for "ghost-step"
        _insert_tool_call(db, "/repo", "test-change", "implement", "ghost-step",
                          "developer", "WebSearch")

        from orchestrator_next.cost_report import _step_allowlist_anomalies
        # Must not raise
        result = _step_allowlist_anomalies(db, "/repo", "test-change")
        assert result == []


