"""T-2 regression-guard: design skill emits tasks.yaml.

Verifies that the skill-step (contract.yaml + <id>/SKILL.md):
  - lists tasks.yaml in outputs
  - mentions tasks.yaml in the instruction body
  - mentions tasks.yaml in the verify block
  - references Tasks YAML Format Contract
"""
from __future__ import annotations

import os

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_STEP_DIR = os.path.join(_REPO_ROOT, "config", "steps", "design")
_STEP_FILE = os.path.join(_STEP_DIR, "contract.yaml")
_SKILL_FILE = os.path.join(_REPO_ROOT, "skills", "architect", "SKILL.md")


def _load_step() -> dict:
    with open(_STEP_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_instruction() -> str:
    """Skill instruction body lives in skills/architect/SKILL.md."""
    step = _load_step()
    if step.get("instruction"):
        return step["instruction"]
    if os.path.isfile(_SKILL_FILE):
        with open(_SKILL_FILE, "r", encoding="utf-8") as f:
            return f.read()
    return ""


class TestDesignAndDraftEmitsTasksYaml:

    def test_tasks_yaml_in_outputs(self):
        """Outputs declaration lives in SKILL.md ## Outputs section."""
        instruction = _load_instruction()
        assert "## Outputs" in instruction and "tasks.yaml" in instruction, (
            "design SKILL.md ## Outputs does not declare 'tasks.yaml'"
        )

    def test_tasks_yaml_in_verify(self):
        """Verify block lives in SKILL.md ## Verify section."""
        instruction = _load_instruction()
        verify_section = instruction.split("## Verify", 1)[-1] if "## Verify" in instruction else ""
        assert "tasks.yaml" in verify_section, (
            "design SKILL.md ## Verify does not reference 'tasks.yaml'"
        )

    def test_tasks_yaml_in_instruction(self):
        """The skill body must mention tasks.yaml."""
        instruction = _load_instruction()
        assert "tasks.yaml" in instruction, (
            "design instruction does not mention 'tasks.yaml'"
        )

    def test_tasks_yaml_format_contract_referenced(self):
        """The instruction block must reference the Tasks YAML Format Contract."""
        instruction = _load_instruction()
        assert "Tasks YAML Format Contract" in instruction, (
            "design instruction does not reference "
            "'Tasks YAML Format Contract'"
        )
