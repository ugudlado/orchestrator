"""T-2 regression-guard: design-and-draft-artifacts contract emits tasks.yaml.

Verifies that the step contract (directory form: contract.yaml + prompt.md):
  - lists tasks.yaml in outputs
  - mentions tasks.yaml in the instruction block (prompt.md in directory form)
  - mentions tasks.yaml in the verify block
  - references artifact-formats.md § Tasks YAML Format Contract
"""
from __future__ import annotations

import os

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))
_STEP_DIR = os.path.join(_REPO_ROOT, "config", "steps", "design-and-draft-artifacts")
_STEP_FILE = os.path.join(_STEP_DIR, "contract.yaml")
_PROMPT_FILE = os.path.join(_STEP_DIR, "prompt.md")


def _load_step() -> dict:
    with open(_STEP_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_instruction() -> str:
    """In the directory form, instruction prose lives in prompt.md."""
    step = _load_step()
    # Flat-file form keeps instruction inline; directory form uses prompt.md.
    if step.get("instruction"):
        return step["instruction"]
    if os.path.isfile(_PROMPT_FILE):
        with open(_PROMPT_FILE, "r", encoding="utf-8") as f:
            return f.read()
    return ""


class TestDesignAndDraftEmitsTasksYaml:

    def test_tasks_yaml_in_outputs(self):
        """ORC-104: outputs declaration moved to prompt.md's ## Outputs section.
        The prompt must declare tasks.yaml as a produced artifact."""
        instruction = _load_instruction()
        assert "## Outputs" in instruction and "tasks.yaml" in instruction, (
            "design-and-draft-artifacts prompt.md ## Outputs does not declare 'tasks.yaml'"
        )

    def test_tasks_yaml_in_verify(self):
        """ORC-104: verify block moved to prompt.md's ## Verify section.
        The prompt must reference tasks.yaml in its verification checks."""
        instruction = _load_instruction()
        verify_section = instruction.split("## Verify", 1)[-1] if "## Verify" in instruction else ""
        assert "tasks.yaml" in verify_section, (
            "design-and-draft-artifacts prompt.md ## Verify does not reference 'tasks.yaml'"
        )

    def test_tasks_yaml_in_instruction(self):
        """The instruction block (contract.yaml or prompt.md) must mention tasks.yaml."""
        instruction = _load_instruction()
        assert "tasks.yaml" in instruction, (
            "design-and-draft-artifacts instruction does not mention 'tasks.yaml'"
        )

    def test_tasks_yaml_format_contract_referenced(self):
        """The instruction block must reference the Tasks YAML Format Contract."""
        instruction = _load_instruction()
        assert "Tasks YAML Format Contract" in instruction, (
            "design-and-draft-artifacts instruction does not reference "
            "'Tasks YAML Format Contract'"
        )
