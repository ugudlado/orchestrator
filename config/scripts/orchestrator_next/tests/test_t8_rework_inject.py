"""T-8 tests: needs_work branch uses expand-plan injection, not execute-next-task reset.

The key behavioral changes in T-8:
1. record.py no longer calls readiness.mark_node_status(..., "execute-next-task", "in_progress")
2. run-phase-review.yaml instruction tells the agent to append fix tasks to tasks.yaml
   and invoke orchestrator expand-plan before returning COMPLETION with needs_work.

RED: test_no_execute_next_task_reset_in_record fails until line 1608 is removed.
"""
from __future__ import annotations

import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))
_RECORD_PY = os.path.join(_REPO_ROOT, "config", "scripts", "orchestrator_next", "record.py")
_RUN_PHASE_REVIEW_YAML = os.path.join(_REPO_ROOT, "config", "steps", "run-phase-review.yaml")


class TestNoExecuteNextTaskResetInRecord:

    def test_no_execute_next_task_reset(self):
        """record.py must not call mark_node_status for 'execute-next-task' status reset."""
        with open(_RECORD_PY, "r") as f:
            content = f.read()
        # The specific call that resets execute-next-task must be gone.
        # We check for 'execute-next-task' inside mark_node_status calls.
        pattern = r'mark_node_status\s*\(.*?"execute-next-task"'
        matches = re.findall(pattern, content, re.DOTALL)
        assert len(matches) == 0, (
            f"record.py still contains mark_node_status call for 'execute-next-task': {matches}"
        )

    def test_no_string_literal_execute_next_task_in_rework_code(self):
        """The rework loop code block must not have a literal 'execute-next-task' node reset."""
        with open(_RECORD_PY, "r") as f:
            lines = f.readlines()
        # Find the rework block (around the old line 1608)
        for i, line in enumerate(lines):
            if '"execute-next-task"' in line and "mark_node_status" in lines[max(0, i-2):i+1]:
                raise AssertionError(
                    f"record.py line {i+1} still has mark_node_status for execute-next-task"
                )


class TestRunPhaseReviewYamlNeedsWork:

    def test_run_phase_review_mentions_tasks_yaml(self):
        """run-phase-review.yaml instruction must mention tasks.yaml for fix task appending."""
        with open(_RUN_PHASE_REVIEW_YAML, "r") as f:
            content = f.read()
        assert "tasks.yaml" in content, (
            "run-phase-review.yaml instruction must mention tasks.yaml "
            "(for appending fix tasks on needs_work)"
        )

    def test_run_phase_review_mentions_expand_plan(self):
        """run-phase-review.yaml instruction must mention expand-plan (or orchestrator expand-plan)."""
        with open(_RUN_PHASE_REVIEW_YAML, "r") as f:
            content = f.read()
        assert "expand-plan" in content, (
            "run-phase-review.yaml instruction must mention 'expand-plan' "
            "(invoked on needs_work to inject fix task-nodes)"
        )
