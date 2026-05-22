"""T-13 RED tests: compute_resolution reads from step_history, not tasks.md.

AC-16: telemetry source of truth shifts from tasks.md checkboxes to per-task
step_history entries.

RED: these tests fail before T-13 implementation because compute_task_counts
does not exist yet.
"""
from __future__ import annotations

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


def test_compute_task_counts_completed_from_step_history():
    """tasks_completed = count of step_history entries with step_id like 'task-%'
    and status in (completed, recovered)."""
    from orchestrator_next.record import compute_task_counts
    step_history = [
        {"step_id": "task-T-1", "status": "completed"},
        {"step_id": "task-T-2", "status": "completed"},
        {"step_id": "task-T-3", "status": "failed"},
        {"step_id": "run-phase-review", "status": "completed"},
    ]
    workflow_plan = {
        "implement": {
            "nodes": [
                {"id": "task-T-1"},
                {"id": "task-T-2"},
                {"id": "task-T-3"},
                {"id": "run-phase-review"},
            ]
        }
    }
    result = compute_task_counts(step_history=step_history, workflow_plan=workflow_plan)
    assert result["tasks_completed"] == 2
    assert result["tasks_total"] == 3


def test_compute_task_counts_recovered_counts_as_completed():
    """status=recovered counts toward tasks_completed."""
    from orchestrator_next.record import compute_task_counts
    step_history = [
        {"step_id": "task-T-1", "status": "recovered"},
        {"step_id": "task-T-2", "status": "completed"},
    ]
    workflow_plan = {
        "implement": {
            "nodes": [
                {"id": "task-T-1"},
                {"id": "task-T-2"},
                {"id": "run-phase-review"},
            ]
        }
    }
    result = compute_task_counts(step_history=step_history, workflow_plan=workflow_plan)
    assert result["tasks_completed"] == 2
    assert result["tasks_total"] == 2


def test_compute_task_counts_failed_not_counted_as_completed():
    """status=failed is counted in tasks_failed, not tasks_completed."""
    from orchestrator_next.record import compute_task_counts
    step_history = [
        {"step_id": "task-T-1", "status": "failed"},
    ]
    workflow_plan = {
        "implement": {
            "nodes": [
                {"id": "task-T-1"},
            ]
        }
    }
    result = compute_task_counts(step_history=step_history, workflow_plan=workflow_plan)
    assert result["tasks_completed"] == 0
    assert result["tasks_failed"] == 1
    assert result["tasks_total"] == 1


def test_compute_task_counts_tasks_added_from_fix_nodes():
    """tasks_added = fix-N nodes in workflow_plan (appended after initial expand-plan)."""
    from orchestrator_next.record import compute_task_counts
    step_history = [
        {"step_id": "task-T-1", "status": "completed"},
        {"step_id": "task-T-2", "status": "completed"},
        {"step_id": "task-fix-1", "status": "completed"},
    ]
    workflow_plan = {
        "implement": {
            "nodes": [
                {"id": "task-T-1"},
                {"id": "task-T-2"},
                {"id": "task-fix-1"},
                {"id": "run-phase-review"},
            ]
        }
    }
    result = compute_task_counts(step_history=step_history, workflow_plan=workflow_plan)
    assert result["tasks_total"] == 3   # T-1, T-2, fix-1
    assert result["tasks_added"] == 1   # fix-1 is added
    assert result["tasks_completed"] == 3


def test_compute_task_counts_no_task_nodes_returns_none():
    """When workflow_plan has no task-nodes, returns None values (spike path)."""
    from orchestrator_next.record import compute_task_counts
    step_history = [
        {"step_id": "run-phase-review", "status": "completed"},
    ]
    workflow_plan = {
        "implement": {
            "nodes": [
                {"id": "design-and-draft-artifacts"},
                {"id": "run-phase-review"},
            ]
        }
    }
    result = compute_task_counts(step_history=step_history, workflow_plan=workflow_plan)
    assert result["tasks_total"] is None
    assert result["tasks_completed"] is None
