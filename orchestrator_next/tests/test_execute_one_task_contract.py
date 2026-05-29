"""T-6 tests: execute-one-task step contract.

Regression-guard: verifies structural properties of the contract file.
RED phase: tests fail before execute-one-task.yaml is created.
"""
from __future__ import annotations

import os

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_CONTRACT_FILE = os.path.join(_REPO_ROOT, "config", "steps", "execute-one-task", "contract.yaml")
_PROMPT_FILE = os.path.join(_REPO_ROOT, "config", "steps", "execute-one-task", "prompt.md")


def _load_contract() -> dict:
    with open(_CONTRACT_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class TestExecuteOneTaskContract:

    def test_file_exists(self):
        assert os.path.isfile(_CONTRACT_FILE), (
            f"execute-one-task.yaml not found at {_CONTRACT_FILE}"
        )

    def test_file_under_80_lines(self):
        with open(_CONTRACT_FILE, "r") as f:
            lines = f.readlines()
        assert len(lines) < 80, (
            f"execute-one-task.yaml has {len(lines)} lines; must be < 80"
        )

    def test_agent_is_developer(self):
        contract = _load_contract()
        assert contract.get("agent") == "developer", (
            f"execute-one-task.yaml agent must be 'developer', got {contract.get('agent')!r}"
        )

    def test_no_repeat_until(self):
        with open(_CONTRACT_FILE, "r") as f:
            content = f.read()
        assert "repeat_until" not in content, (
            "execute-one-task.yaml must not contain 'repeat_until'"
        )

    def test_references_step_context_task(self):
        # ORC-104: instruction prose moved from contract.yaml to prompt.md.
        with open(_PROMPT_FILE, "r") as f:
            content = f.read()
        assert "step_context.task" in content, (
            "execute-one-task prompt.md instruction must reference 'step_context.task'"
        )

    def test_no_tasks_md_in_inputs(self):
        contract = _load_contract()
        inputs = contract.get("inputs", [])
        inputs_str = " ".join(str(i) for i in inputs)
        assert "tasks.md" not in inputs_str, (
            "execute-one-task.yaml inputs must not reference tasks.md"
        )
