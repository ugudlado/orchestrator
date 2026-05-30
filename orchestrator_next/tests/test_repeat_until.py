"""T-1: Regression test — repeat_until enforcement in _compute_next_step (RED).

Three cases that prove ISSUE-16:
  1. re-emits execute-next-task when unchecked items remain in tasks.md  (RED on main)
  2. advances to run-phase-review when all tasks are checked              (currently passes)
  3. advances when the contract has no repeat_until key                   (currently passes)

Tests 2 and 3 serve as regression guards so the fix in T-2 doesn't break
the normal advance-through-active[] behavior.
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

from orchestrator_next.record import record  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STUB_CONTRACT_WITH_REPEAT_UNTIL = textwrap.dedent("""\
    id: execute-next-task
    agent: developer
    instruction: Execute the next task.
    rules: []
    inputs: []
    outputs:
      - task_execution_result
    repeat_until: all_tasks_completed
""")

_STUB_CONTRACT_WITHOUT_REPEAT_UNTIL = textwrap.dedent("""\
    id: execute-next-task
    agent: developer
    instruction: Execute the next task.
    rules: []
    inputs: []
    outputs:
      - task_execution_result
""")


def _write_state(tmp_path, tasks_md_path: str) -> str:
    """Write a minimal state.yaml (ORC-63 nodes shape) with execute-next-task
    and run-phase-review nodes; return its path."""
    state = {
        "change_id": "repro-16",
        "phase": "implement",
        "workflow_plan": {
            "implement": {
                "nodes": [
                    {"id": "execute-next-task", "status": "pending",
                     "agent": "developer", "goal": "", "inputs": [],
                     "outputs": ["task_execution_result"], "rules": []},
                    {"id": "run-phase-review", "status": "pending",
                     "agent": "reviewer", "goal": "", "inputs": [],
                     "outputs": [], "rules": []},
                ],
                "filtered": [],
            }
        },
        "step_history": [],
        "tasks_path": tasks_md_path,
        "worktree_path": str(tmp_path),
    }
    path = tmp_path / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(path)


def _payload() -> dict:
    """Minimal valid payload for recording execute-next-task as completed."""
    return {
        "step_id": "execute-next-task",
        "phase": "implement",
        "status": "completed",
        "agent": "developer",
        "outputs": {"task_execution_result": {"task_id": "T-1", "status": "completed"}},
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestRepeatUntil:

    def test_unknown_predicate_does_not_repeat(self, tmp_path, monkeypatch):
        """ORC-65 T-9: all_tasks_completed removed from REPEAT_PREDICATES.
        When a stub contract declares repeat_until: all_tasks_completed, the
        predicate is unknown → treated as satisfied → step advances (no repeat).
        """
        # Write stub contract with repeat_until: all_tasks_completed
        contracts_dir = tmp_path / "contracts"
        contracts_dir.mkdir()
        (contracts_dir / "execute-next-task.yaml").write_text(
            _STUB_CONTRACT_WITH_REPEAT_UNTIL
        )
        monkeypatch.setenv(
            "ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts_dir)
        )

        # tasks.md with two unchecked items — but predicate is unknown, so no repeat
        tasks_md = tmp_path / "tasks.md"
        tasks_md.write_text("- [ ] T-1: Fix the bug\n- [ ] T-2: Write tests\n")

        state_path = _write_state(tmp_path, str(tasks_md))
        result, _exit_code = record(state_path, _payload())

        next_step = (result.get("next_step") or {}).get("step_id")
        assert next_step == "run-phase-review", (
            f"Expected next_step='run-phase-review' (unknown predicate = satisfied, no repeat), "
            f"got {next_step!r}."
        )

    def test_advances_when_all_tasks_checked(self, tmp_path, monkeypatch):
        """When every task in tasks.md is checked, execute-next-task should
        not repeat — next_step should advance to run-phase-review."""
        contracts_dir = tmp_path / "contracts"
        contracts_dir.mkdir()
        (contracts_dir / "execute-next-task.yaml").write_text(
            _STUB_CONTRACT_WITH_REPEAT_UNTIL
        )
        monkeypatch.setenv(
            "ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts_dir)
        )

        # tasks.md with all items checked
        tasks_md = tmp_path / "tasks.md"
        tasks_md.write_text("- [x] T-1: Fix the bug\n- [x] T-2: Write tests\n")

        state_path = _write_state(tmp_path, str(tasks_md))
        result, _exit_code = record(state_path, _payload())

        next_step = (result.get("next_step") or {}).get("step_id")
        assert next_step == "run-phase-review", (
            f"Expected next_step='run-phase-review' (advance after completion), "
            f"got {next_step!r}."
        )

    def test_no_repeat_when_contract_lacks_repeat_until(self, tmp_path, monkeypatch):
        """Baseline: a contract without repeat_until must not trigger repeat logic.
        The step should advance normally — preserving existing behavior."""
        contracts_dir = tmp_path / "contracts"
        contracts_dir.mkdir()
        (contracts_dir / "execute-next-task.yaml").write_text(
            _STUB_CONTRACT_WITHOUT_REPEAT_UNTIL
        )
        monkeypatch.setenv(
            "ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts_dir)
        )

        # tasks.md with unchecked items — but contract has no repeat_until, so
        # should NOT cause a repeat
        tasks_md = tmp_path / "tasks.md"
        tasks_md.write_text("- [ ] T-1: Fix the bug\n- [ ] T-2: Write tests\n")

        state_path = _write_state(tmp_path, str(tasks_md))
        result, _exit_code = record(state_path, _payload())

        next_step = (result.get("next_step") or {}).get("step_id")
        assert next_step == "run-phase-review", (
            f"Expected next_step='run-phase-review' (no repeat_until in contract), "
            f"got {next_step!r}."
        )
