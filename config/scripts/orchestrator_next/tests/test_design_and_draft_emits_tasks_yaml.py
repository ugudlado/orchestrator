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
        """design-and-draft-artifacts contract must declare tasks.yaml in outputs
        (either as legacy string 'tasks.yaml' or typed dict with path ending in tasks.yaml)."""
        step = _load_step()
        outputs = step.get("outputs", [])
        found = "tasks.yaml" in outputs or any(
            isinstance(o, dict) and str(o.get("path", "")).endswith("tasks.yaml")
            for o in outputs
        )
        assert found, (
            f"design-and-draft-artifacts contract outputs does not include 'tasks.yaml'. "
            f"Got: {outputs}"
        )

    def test_tasks_yaml_in_verify(self):
        """design-and-draft-artifacts contract verify block must reference tasks.yaml."""
        step = _load_step()
        verify_items = step.get("verify", [])
        verify_text = "\n".join(str(v) for v in verify_items)
        assert "tasks.yaml" in verify_text, (
            "design-and-draft-artifacts contract verify block does not reference 'tasks.yaml'"
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
