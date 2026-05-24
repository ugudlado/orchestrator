"""T-2 regression-guard: design-and-draft-artifacts.yaml emits tasks.yaml.

Verifies that the step contract:
  - lists tasks.yaml in outputs
  - mentions tasks.yaml in the instruction block
  - mentions tasks.yaml in the verify block
  - references artifact-formats.md § Tasks YAML Format Contract
"""
from __future__ import annotations

import os

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))
_STEP_FILE = os.path.join(
    _REPO_ROOT, "config", "steps", "design-and-draft-artifacts.yaml"
)


def _load_step() -> dict:
    with open(_STEP_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class TestDesignAndDraftEmitsTasksYaml:

    def test_tasks_yaml_in_outputs(self):
        """design-and-draft-artifacts.yaml must declare tasks.yaml in outputs."""
        step = _load_step()
        outputs = step.get("outputs", [])
        assert "tasks.yaml" in outputs, (
            f"design-and-draft-artifacts.yaml outputs does not include 'tasks.yaml'. "
            f"Got: {outputs}"
        )

    def test_tasks_yaml_in_verify(self):
        """design-and-draft-artifacts.yaml verify block must reference tasks.yaml."""
        step = _load_step()
        verify_items = step.get("verify", [])
        verify_text = "\n".join(str(v) for v in verify_items)
        assert "tasks.yaml" in verify_text, (
            "design-and-draft-artifacts.yaml verify block does not reference 'tasks.yaml'"
        )

    def test_tasks_yaml_in_instruction(self):
        """The instruction block must mention tasks.yaml (directing the architect to write it)."""
        step = _load_step()
        instruction = step.get("instruction", "")
        assert "tasks.yaml" in instruction, (
            "design-and-draft-artifacts.yaml instruction block does not mention 'tasks.yaml'"
        )

    def test_tasks_yaml_format_contract_referenced(self):
        """The instruction block must reference the Tasks YAML Format Contract."""
        step = _load_step()
        instruction = step.get("instruction", "")
        assert "Tasks YAML Format Contract" in instruction, (
            "design-and-draft-artifacts.yaml instruction does not reference "
            "'Tasks YAML Format Contract'"
        )
