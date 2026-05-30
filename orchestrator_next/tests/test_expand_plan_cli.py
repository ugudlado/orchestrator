"""T-4 tests: orchestrator expand-plan CLI subcommand.

RED phase: tests fail before expand-plan verb is wired into bin/orchestrator.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_ORCHESTRATOR_BIN = os.path.join(_REPO_ROOT, "bin", "orchestrator")


MINIMAL_TASKS_YAML = {
    "version": 1,
    "tasks": [
        {
            "id": "T-1",
            "title": "Wire X",
            "files": ["a.py"],
            "verify": ["echo ok"],
            "depends_on": [],
        },
    ],
}


def _make_state(tmp_path) -> str:
    spec_changes = tmp_path / "spec" / "changes" / "test-feature"
    spec_changes.mkdir(parents=True)
    (spec_changes / "tasks.yaml").write_text(
        yaml.safe_dump(MINIMAL_TASKS_YAML, sort_keys=False, default_flow_style=False)
    )
    state = {
        "change_id": "test-feature",
        "phase": "implement",
        "schema": "feature",
        "repo_root": str(tmp_path),
        "worktree_path": str(tmp_path),
        "workflow_plan": {
            "implement": {
                "nodes": [
                    {"id": "design-and-draft-artifacts", "status": "completed",
                     "agent": "architect", "goal": "", "inputs": [], "outputs": [], "rules": []},
                    {"id": "expand-plan", "status": "completed",
                     "agent": None, "goal": "", "inputs": [], "outputs": [], "rules": []},
                    {"id": "run-phase-review", "status": "pending",
                     "agent": "reviewer", "goal": "", "inputs": [],
                     "outputs": ["phase_review_report"], "rules": [],
                     "depends_on": ["expand-plan"]},
                ],
                "filtered": [],
            }
        },
        "step_history": [],
    }
    state_path = tmp_path / "state.yaml"
    state_path.write_text(yaml.safe_dump(state, sort_keys=False, default_flow_style=False))
    return str(state_path)


class TestExpandPlanCLI:

    def test_exits_0_on_success(self, tmp_path):
        """orchestrator expand-plan <state.yaml> exits 0 on success."""
        state_path = _make_state(tmp_path)
        result = subprocess.run(
            [sys.executable, _ORCHESTRATOR_BIN, "expand-plan", state_path],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"expand-plan should exit 0 on success. stderr: {result.stderr}"
        )

    def test_no_arg_exits_nonzero_with_usage(self):
        """orchestrator expand-plan (no arg) exits non-zero with usage message."""
        result = subprocess.run(
            [sys.executable, _ORCHESTRATOR_BIN, "expand-plan"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0, (
            "expand-plan with no arg should exit non-zero"
        )
        # Should emit usage/error message
        output = result.stdout + result.stderr
        assert len(output) > 0, "expand-plan with no arg should emit some output"

    def test_nonexistent_path_exits_nonzero(self, tmp_path):
        """orchestrator expand-plan /nonexistent exits non-zero with file-not-found."""
        result = subprocess.run(
            [sys.executable, _ORCHESTRATOR_BIN, "expand-plan", str(tmp_path / "nonexistent.yaml")],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0, (
            "expand-plan with nonexistent path should exit non-zero"
        )
