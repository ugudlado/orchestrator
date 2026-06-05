"""Tests for compute_task_counts — reads from tasks.yaml status fields.

implement-tasks writes status: completed per task after each commit.
compute_task_counts reads tasks.yaml as the single source of truth.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def _write_tasks(tmp_path: Path, tasks: list[dict]) -> Path:
    p = tmp_path / "tasks.yaml"
    p.write_text(yaml.safe_dump({"version": 1, "tasks": tasks}))
    return p


def test_compute_task_counts_all_pending(tmp_path):
    from orchestrator_next.record import compute_task_counts
    p = _write_tasks(tmp_path, [
        {"id": "T-1", "title": "a", "status": "pending", "files": [], "verify": []},
        {"id": "T-2", "title": "b", "status": "pending", "files": [], "verify": []},
    ])
    result = compute_task_counts(p)
    assert result["tasks_total"] == 2
    assert result["tasks_completed"] == 0
    assert result["tasks_failed"] == 2
    assert result["resolve_rate"] == 0.0


def test_compute_task_counts_some_completed(tmp_path):
    from orchestrator_next.record import compute_task_counts
    p = _write_tasks(tmp_path, [
        {"id": "T-1", "title": "a", "status": "completed", "files": [], "verify": []},
        {"id": "T-2", "title": "b", "status": "completed", "files": [], "verify": []},
        {"id": "T-3", "title": "c", "status": "pending", "files": [], "verify": []},
    ])
    result = compute_task_counts(p)
    assert result["tasks_total"] == 3
    assert result["tasks_completed"] == 2
    assert result["tasks_failed"] == 1
    assert round(result["resolve_rate"], 4) == round(2 / 3, 4)


def test_compute_task_counts_fix_tasks_counted(tmp_path):
    from orchestrator_next.record import compute_task_counts
    p = _write_tasks(tmp_path, [
        {"id": "T-1", "title": "a", "status": "completed", "files": [], "verify": []},
        {"id": "T-2", "title": "b", "status": "completed", "files": [], "verify": []},
        {"id": "fix-1", "title": "fix", "status": "completed", "files": [], "verify": []},
    ])
    result = compute_task_counts(p)
    assert result["tasks_total"] == 3
    assert result["tasks_planned"] == 2
    assert result["tasks_added"] == 1
    assert result["tasks_completed"] == 3


def test_compute_task_counts_no_status_field_counts_as_pending(tmp_path):
    """Tasks without a status field are treated as pending."""
    from orchestrator_next.record import compute_task_counts
    p = _write_tasks(tmp_path, [
        {"id": "T-1", "title": "a", "files": [], "verify": []},
    ])
    result = compute_task_counts(p)
    assert result["tasks_total"] == 1
    assert result["tasks_completed"] == 0


def test_compute_task_counts_missing_file_returns_none():
    from orchestrator_next.record import compute_task_counts
    result = compute_task_counts(Path("/nonexistent/tasks.yaml"))
    assert result["tasks_total"] is None
    assert result["tasks_completed"] is None


def test_compute_task_counts_none_path_returns_none():
    from orchestrator_next.record import compute_task_counts
    result = compute_task_counts(None)
    assert result["tasks_total"] is None
