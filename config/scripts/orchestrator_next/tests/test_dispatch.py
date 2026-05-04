"""
Regression test: dispatch.dispatch() must re-emit execute-next-task when
repeat_until predicate returns False (unchecked tasks remain).

T-1 Extension (added during T-2 architect consultation — see escalation_events
in state.yaml). This test:

  - FAILS on main (dispatch.py:314-319 ignores repeat_until, advances to
    run-phase-review).
  - FAILS after T-2 (record.py-only fix; dispatch is still broken).
  - PASSES after T-2.5 (dispatch.py re-emits execute-next-task when predicate
    is False).

Bug: dispatch._find_completed_step returns True for execute-next-task (it has
a completed history entry). dispatch then treats it as advanced and picks
run-phase-review as next_step_id — without consulting contract.repeat_until or
evaluating _check_all_tasks_completed.

Fix (T-2.5): in the history-walk loop, when a step has a completed entry AND
its contract declares repeat_until, evaluate the predicate; if False, re-emit
that step (not the successor).
"""
from __future__ import annotations

import os
import sys
import textwrap

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from orchestrator_next.parser import State, StepHistoryEntry  # noqa: E402
from orchestrator_next.dispatch import dispatch  # noqa: E402


# ---------------------------------------------------------------------------
# Contract YAML stubs
# ---------------------------------------------------------------------------

_CONTRACT_EXECUTE_NEXT_TASK = textwrap.dedent("""\
    id: execute-next-task
    agent: developer
    instruction: Execute the next pending task.
    rules: []
    inputs: []
    outputs:
      - task_execution_result
    repeat_until: all_tasks_completed
""")

_CONTRACT_RUN_PHASE_REVIEW = textwrap.dedent("""\
    id: run-phase-review
    agent: developer
    instruction: Review the completed phase.
    rules: []
    inputs: []
    outputs: []
""")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_contracts(steps_dir) -> None:
    """Write both step contracts to steps_dir."""
    (steps_dir / "execute-next-task.yaml").write_text(_CONTRACT_EXECUTE_NEXT_TASK)
    (steps_dir / "run-phase-review.yaml").write_text(_CONTRACT_RUN_PHASE_REVIEW)


def _write_plan_yaml(state_dir, phase: str = "implement") -> None:
    """Write a plan.yaml with both active steps in the given phase."""
    plan = {
        "feature": "hl-303-repro",
        "schema": "feature",
        "resolved_flags": {},
        "phases": [
            {
                "name": phase,
                "goal": "Execute all pending tasks.",
                "steps": [
                    {
                        "id": "execute-next-task",
                        "agent": "developer",
                        "goal": "Execute the next pending task.",
                        "inputs": [],
                        "outputs": ["task_execution_result"],
                        "rules": [],
                    },
                    {
                        "id": "run-phase-review",
                        "agent": "developer",
                        "goal": "Review the completed phase.",
                        "inputs": [],
                        "outputs": [],
                        "rules": [],
                    },
                ],
            }
        ],
    }
    (state_dir / "plan.yaml").write_text(yaml.safe_dump(plan, sort_keys=False))


def _make_state(tasks_md_path: str, phase: str = "implement") -> State:
    """
    Build a State with execute-next-task already completed in step_history.

    state.raw includes tasks_path so _resolve_tasks_md finds the file using
    the explicit-override path (the simplest resolution path, honored before
    any worktree/repo_root derivation).
    """
    completed_entry = StepHistoryEntry(
        step_id="execute-next-task",
        phase=phase,
        status="completed",
        agent="developer",
        attempt=1,
        started_at="2026-05-01T10:00:00Z",
        ended_at="2026-05-01T11:00:00Z",
        usage={"total_tokens": 100},
        escalation=None,
        raw={
            "step_id": "execute-next-task",
            "phase": phase,
            "status": "completed",
            "agent": "developer",
            "attempt": 1,
            "started_at": "2026-05-01T10:00:00Z",
            "ended_at": "2026-05-01T11:00:00Z",
        },
    )
    raw = {
        "change_id": "hl-303-repro",
        "phase": phase,
        "tasks_path": tasks_md_path,  # explicit override — resolver honors this first
    }
    return State(
        change_id="hl-303-repro",
        phase=phase,
        repo_root="/repo",
        workflow_dir="/workflow",
        workflow_plan={
            phase: {
                "active": ["execute-next-task", "run-phase-review"],
            }
        },
        step_history=[completed_entry],
        raw=raw,
    )


# ---------------------------------------------------------------------------
# Regression test
# ---------------------------------------------------------------------------

def test_dispatch_repeats_step_when_predicate_false(tmp_path, monkeypatch):
    """
    dispatch.dispatch() must re-emit execute-next-task when:
      - execute-next-task has a completed step_history entry (predicate
        evaluation is triggered, not the "no history" first-run branch).
      - The step's contract declares repeat_until: all_tasks_completed.
      - tasks.md has at least one unchecked item (predicate returns False).

    FAILS on main: dispatch.py:314-319 picks run-phase-review (ignores
    repeat_until). PASSES after T-2.5 re-emits execute-next-task.
    """
    # Write step contracts and set override env so dispatch finds them.
    steps_dir = tmp_path / "steps"
    steps_dir.mkdir()
    _write_contracts(steps_dir)
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(steps_dir))

    # Write tasks.md with at least one unchecked item.
    tasks_md = tmp_path / "tasks.md"
    tasks_md.write_text(
        "- [x] T-1: Regression test written\n"
        "- [ ] T-2: Fix root cause in record.py\n"
        "- [ ] T-2.5: Fix dispatch.py second seam\n"
    )

    # Write state.yaml + plan.yaml so _load_plan succeeds.
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    state_yaml_path = str(state_dir / "state.yaml")
    (state_dir / "state.yaml").write_text(
        yaml.safe_dump({"change_id": "hl-303-repro", "phase": "implement"})
    )
    _write_plan_yaml(state_dir)

    state = _make_state(tasks_md_path=str(tasks_md))

    action, exit_code = dispatch(state, state_yaml_path)

    assert exit_code == 0, (
        f"Expected exit_code=0, got {exit_code}. action={action!r}"
    )
    assert action.get("step_id") == "execute-next-task", (
        f"dispatch() must re-emit 'execute-next-task' while unchecked tasks "
        f"remain (repeat_until: all_tasks_completed). "
        f"Got step_id={action.get('step_id')!r}. "
        f"Bug: dispatch.py history-walk ignores contract.repeat_until and "
        f"advances to 'run-phase-review' prematurely."
    )
