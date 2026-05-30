"""Tests for complete_phase.prepare_complete_phase."""
from __future__ import annotations

import os
import sys

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, os.path.join(_REPO_ROOT, "config", "scripts"))

from orchestrator_next.complete_phase import (  # noqa: E402
    complete_step_ids_for_schema,
    prepare_complete_phase,
)


def test_complete_step_ids_feature_schema():
    steps = complete_step_ids_for_schema("feature")
    assert steps[0] == "compute-prediction-accuracy"
    assert steps[-1] == "archive-completed-change"
    assert "cost-report" in steps
    assert "ticket-done" in steps
    assert "archive-completed-change" in steps


def test_complete_step_ids_complete_workflow_schema(monkeypatch):
    """config/workflows/complete.yaml is the CLI complete subcommand step list."""
    monkeypatch.setenv("ORCHESTRATOR_HOME", _REPO_ROOT)
    steps = complete_step_ids_for_schema("complete")
    assert steps == [
        "compute-prediction-accuracy",
        "run-learn-cycle",
        "ticket-qa",
        "mark-change-completed",
        "compute-swe-metrics",
        "cost-report",
        "ticket-done",
        "archive-completed-change",
    ]


def test_prepare_blocks_incomplete_task(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", _REPO_ROOT)
    state_path = tmp_path / "state.yaml"
    state_path.write_text(
        yaml.safe_dump(
            {
                "change_id": "demo",
                "schema": "feature",
                "status": "active",
                "phase": "main",
                "workflow_plan": {
                    "main": {
                        "nodes": [
                            {"id": "task-T-1", "status": "pending"},
                            {"id": "archive-completed-change", "status": "pending"},
                        ],
                        "filtered": [],
                    }
                },
            },
            sort_keys=False,
        )
    )
    with pytest.raises(ValueError, match="implement phase must finish"):
        prepare_complete_phase(str(state_path))


def test_prepare_marks_prior_nodes_completed(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", _REPO_ROOT)
    state_path = tmp_path / "state.yaml"
    state_path.write_text(
        yaml.safe_dump(
            {
                "change_id": "demo",
                "schema": "feature",
                "status": "active",
                "phase": "main",
                "workflow_plan": {
                    "main": {
                        "nodes": [
                            {"id": "task-T-1", "status": "completed"},
                            {"id": "run-phase-review", "status": "completed"},
                            {"id": "mark-change-completed", "status": "pending"},
                            {"id": "archive-completed-change", "status": "pending"},
                        ],
                        "filtered": [],
                    }
                },
            },
            sort_keys=False,
        )
    )
    summary = prepare_complete_phase(str(state_path))
    assert summary["next_step"] == "mark-change-completed"
    state = yaml.safe_load(state_path.read_text())
    assert state["next_step"]["step_id"] == "mark-change-completed"
    by_id = {n["id"]: n["status"] for n in state["workflow_plan"]["main"]["nodes"]}
    assert by_id["run-phase-review"] == "completed"
    assert by_id["mark-change-completed"] == "pending"
