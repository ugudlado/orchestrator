"""Tests for spawn_failure_cap auto-resume on run-workflow entry (orc-85 follow-up)."""
from __future__ import annotations

import os
import sys


_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from orchestrator_next.dispatch import dispatch  # noqa: E402
from orchestrator_next.parser import load_state  # noqa: E402
from orchestrator_next.spawn_resume import (  # noqa: E402
    apply_spawn_failure_resume,
    clear_spawn_failure_cap_in_raw,
)
from orchestrator_next.tests.test_dispatch_retry_storm import (  # noqa: E402
    _promoted_plan_state,
    _setup,
    _spawn_failure_entry,
    _task_node,
)


def test_clear_spawn_failure_cap_removes_trailing_failures_and_unblocks(tmp_path):
    state = _promoted_plan_state(
        tmp_path,
        nodes=[_task_node("task-T-1", status="in_progress")],
        step_history=[
            _spawn_failure_entry("task-T-1", "main", 1),
            _spawn_failure_entry("task-T-1", "main", 2),
            _spawn_failure_entry("task-T-1", "main", 3),
        ],
    )
    state["status"] = "blocked"
    state["next_step"] = {"phase": "main", "step_id": "task-T-1"}

    assert clear_spawn_failure_cap_in_raw(state) is True
    assert state["status"] == "in_progress"
    assert state["step_history"] == []


def test_clear_spawn_failure_cap_noop_when_below_cap(tmp_path):
    state = _promoted_plan_state(
        tmp_path,
        nodes=[_task_node("task-T-1", status="pending")],
        step_history=[
            _spawn_failure_entry("task-T-1", "main", 1),
            _spawn_failure_entry("task-T-1", "main", 2),
        ],
    )
    state["status"] = "blocked"
    assert clear_spawn_failure_cap_in_raw(state) is False
    assert len(state["step_history"]) == 2


def test_apply_spawn_failure_resume_allows_dispatch(tmp_path, monkeypatch):
    state = _promoted_plan_state(
        tmp_path,
        nodes=[_task_node("task-T-1", status="in_progress")],
        step_history=[
            _spawn_failure_entry("task-T-1", "main", 1),
            _spawn_failure_entry("task-T-1", "main", 2),
            _spawn_failure_entry("task-T-1", "main", 3),
        ],
    )
    state["status"] = "blocked"
    state["next_step"] = {"phase": "main", "step_id": "task-T-1"}
    state_path = _setup(tmp_path, monkeypatch, state)

    _, code_before = dispatch(load_state(state_path), state_path)
    assert code_before == 2

    assert apply_spawn_failure_resume(state_path) is True

    action, code_after = dispatch(load_state(state_path), state_path)
    assert code_after == 0
    assert action.get("step_id") == "task-T-1"
    assert action.get("reason") != "spawn_failure_cap"
