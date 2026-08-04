"""T-9 tests: execute-next-task contract deleted, all_tasks_completed removed.

RED: tests fail before T-9 implementation.
"""
from __future__ import annotations

import os

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_CONTRACT_FILE = os.path.join(_REPO_ROOT, "config", "steps", "execute-next-task.yaml")
_RECORD_PY = os.path.join(_REPO_ROOT, "orchestrator_next", "record.py")


class TestExecuteNextTaskDeleted:

    def test_execute_next_task_contract_does_not_exist(self):
        """execute-next-task.yaml must not exist in config/steps/."""
        assert not os.path.isfile(_CONTRACT_FILE), (
            f"execute-next-task.yaml still exists at {_CONTRACT_FILE}"
        )

    def test_no_all_tasks_completed_in_record_py(self):
        """record.py must not contain 'all_tasks_completed' (removed with T-9)."""
        if not os.path.exists(_RECORD_PY):
            pytest.skip("engine source not present in pack checkout")
        with open(_RECORD_PY, "r") as f:
            content = f.read()
        assert "all_tasks_completed" not in content, (
            "record.py still contains 'all_tasks_completed'"
        )

    def test_no_check_all_tasks_completed_in_record_py(self):
        """record.py must not contain '_check_all_tasks_completed' function."""
        if not os.path.exists(_RECORD_PY):
            pytest.skip("engine source not present in pack checkout")
        with open(_RECORD_PY, "r") as f:
            content = f.read()
        assert "_check_all_tasks_completed" not in content, (
            "record.py still contains '_check_all_tasks_completed' function"
        )

    def test_no_execute_next_task_in_workflows(self):
        """No execute-next-task step in config/workflows/*.yaml files."""
        import subprocess
        result = subprocess.run(
            ["grep", "-r", "execute-next-task",
             os.path.join(_REPO_ROOT, "config", "workflows")],
            capture_output=True, text=True
        )
        # grep returns 0 if found, 1 if not found
        assert result.returncode != 0, (
            f"execute-next-task still referenced in config/workflows/:\n{result.stdout}"
        )

    def test_no_execute_next_task_yaml_in_steps(self):
        """execute-next-task.yaml must not exist in config/steps/."""
        import subprocess
        result = subprocess.run(
            ["find", os.path.join(_REPO_ROOT, "config", "steps"),
             "-name", "execute-next-task.yaml"],
            capture_output=True, text=True
        )
        assert result.stdout.strip() == "", (
            f"execute-next-task.yaml found: {result.stdout.strip()}"
        )
