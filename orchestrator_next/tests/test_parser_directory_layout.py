"""
Tests for parser.load_contract_for_step directory-form loading.

Scenarios covered:
  1. Directory <id>/contract.yaml with sibling prompt.md loads:
     kind == 'agent', instruction == prompt.md contents.
  2. Directory <id>/contract.yaml with kind: agent but missing prompt.md
     raises ContractError.

AC-1, AC-6 (design.md)
"""
from __future__ import annotations

import os
import sys

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def steps_dir(tmp_path):
    """Create a temp steps directory."""
    d = tmp_path / "steps"
    d.mkdir()
    return d


@pytest.fixture(autouse=True)
def set_override(steps_dir, monkeypatch):
    """Point load_contract_for_step to the temp steps dir."""
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(steps_dir))


def _write_dir_contract(
    steps_dir,
    step_id: str,
    contract_data: dict,
    prompt_text: str | None = None,
    script_text: str | None = None,
) -> object:
    """Write a directory-form contract with optional payload siblings.

    Returns the step directory Path so callers can assert resolved paths.
    """
    step_dir = steps_dir / step_id
    step_dir.mkdir(parents=True, exist_ok=True)
    (step_dir / "contract.yaml").write_text(yaml.dump(contract_data))
    if prompt_text is not None:
        (step_dir / "prompt.md").write_text(prompt_text)
    if script_text is not None:
        (step_dir / "script.sh").write_text(script_text)
    return step_dir


# ---------------------------------------------------------------------------
# TestAgentKindContractLoad: directory-form + agent kind
# ---------------------------------------------------------------------------

class TestAgentKindContractLoad:
    """Tests for directory-form contract loading with kind: agent."""

    def test_agent_dir_contract_loads_kind_and_instruction(self, steps_dir):
        """Scenario 1: directory form with prompt.md loads kind=='agent' and instruction.

        AC-1: given config/steps/<id>/contract.yaml + prompt.md, load_contract_for_step
        returns StepContract with kind == 'agent' and instruction == prompt.md contents.

        Currently RED: load_contract_for_step only looks for <id>.yaml, never <id>/contract.yaml,
        so it raises FileNotFoundError (or fails AttributeError on .kind).
        """
        prompt_text = "You are the discoverer agent. Explore the codebase.\n"
        _write_dir_contract(steps_dir, "explore", {
            "id": "explore",
            "version": 1,
            "kind": "agent",
            "agent": "discoverer",
            "inputs": [],
            "outputs": ["discovery_result"],
            "rules": [],
        }, prompt_text=prompt_text)

        from orchestrator_next.parser import load_contract_for_step, AgentStepContract
        contract = load_contract_for_step("explore")
        assert isinstance(contract, AgentStepContract)
        assert contract.instruction == prompt_text

    def test_agent_dir_contract_prefers_pack_prompt_md(self, steps_dir):
        """pack/prompt.md wins over a root prompt.md — steps-as-packs layout;
        root prompt.md remains as a fallback for unmigrated vendored configs."""
        step_dir = _write_dir_contract(steps_dir, "explore", {
            "id": "explore", "version": 1, "kind": "agent", "agent": "discoverer",
            "inputs": [], "outputs": ["discovery_result"], "rules": [],
        }, prompt_text="Legacy root prompt.\n")
        pack_dir = step_dir / "pack"
        pack_dir.mkdir()
        (pack_dir / "prompt.md").write_text("Pack prompt.\n")

        from orchestrator_next.parser import load_contract_for_step
        contract = load_contract_for_step("explore")
        assert contract.instruction == "Pack prompt.\n"

    def test_agent_dir_contract_inlines_sibling_learnings_md(self, steps_dir):
        """A sibling learnings.md is appended to the loaded instruction, separate
        from prompt.md so a pack upgrade overwriting prompt.md doesn't clobber it."""
        prompt_text = "You are the discoverer agent. Explore the codebase.\n"
        step_dir = _write_dir_contract(steps_dir, "explore", {
            "id": "explore", "version": 1, "kind": "agent", "agent": "discoverer",
            "inputs": [], "outputs": ["discovery_result"], "rules": [],
        }, prompt_text=prompt_text)
        learnings_text = "- Prefer README-derived scope when no ticket body exists.\n"
        (step_dir / "learnings.md").write_text(learnings_text)

        from orchestrator_next.parser import load_contract_for_step
        contract = load_contract_for_step("explore")
        assert prompt_text in contract.instruction
        assert learnings_text.strip() in contract.instruction

    def test_agent_dir_contract_no_learnings_md_is_unaffected(self, steps_dir):
        """Absence of learnings.md leaves instruction exactly as prompt.md wrote it."""
        prompt_text = "You are the discoverer agent. Explore the codebase.\n"
        _write_dir_contract(steps_dir, "explore", {
            "id": "explore", "version": 1, "kind": "agent", "agent": "discoverer",
            "inputs": [], "outputs": ["discovery_result"], "rules": [],
        }, prompt_text=prompt_text)

        from orchestrator_next.parser import load_contract_for_step
        contract = load_contract_for_step("explore")
        assert contract.instruction == prompt_text

    def test_agent_dir_contract_missing_prompt_raises_contract_error(self, steps_dir):
        """Scenario 2: directory form with kind: agent but missing prompt.md raises ContractError.

        AC-6: load_contract_for_step must raise ContractError (not FileNotFoundError) when
        the step directory exists with contract.yaml but prompt.md is absent.

        Currently RED: load_contract_for_step never enters the directory-form branch, so
        it raises FileNotFoundError (no <id>.yaml) instead of ContractError.
        """
        _write_dir_contract(steps_dir, "no-prompt", {
            "id": "no-prompt",
            "version": 1,
            "kind": "agent",
            "agent": "discoverer",
            "inputs": [],
            "outputs": [],
            "rules": [],
        }, prompt_text=None)  # deliberately no prompt.md

        from orchestrator_next.parser import load_contract_for_step, ContractError
        with pytest.raises(ContractError, match="prompt: <dir>"):
            load_contract_for_step("no-prompt")

    def test_prompt_dir_skill_md_preferred_and_frontmatter_stripped(
        self, steps_dir, tmp_path, monkeypatch
    ):
        """prompt: resolves a directory; SKILL.md wins; YAML frontmatter is stripped."""
        skills = tmp_path / "skills"
        skill_dir = skills / "explore"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: explore\ndescription: test\n---\n\nSkill body here.\n"
        )
        monkeypatch.setenv("ORCHESTRATOR_SKILLS_TEST_OVERRIDE", str(skills))
        _write_dir_contract(steps_dir, "explore", {
            "id": "explore", "version": 1, "model": "sonnet", "prompt": "explore",
        }, prompt_text=None)

        from orchestrator_next.parser import load_contract_for_step
        contract = load_contract_for_step("explore")
        assert contract.instruction == "Skill body here.\n"
        assert "name: explore" not in contract.instruction
        assert contract.prompt_dir == str(skill_dir.resolve())

    def test_prompt_field_loads_directory_with_prompt_md(
        self, steps_dir, tmp_path, monkeypatch
    ):
        skills = tmp_path / "skills"
        prompt_dir = skills / "one-off"
        prompt_dir.mkdir(parents=True)
        (prompt_dir / "prompt.md").write_text("Local charter body.\n")
        monkeypatch.setenv("ORCHESTRATOR_SKILLS_TEST_OVERRIDE", str(skills))
        _write_dir_contract(steps_dir, "one-off", {
            "id": "one-off", "version": 1, "model": "sonnet", "prompt": "one-off",
        }, prompt_text=None)

        from orchestrator_next.parser import load_contract_for_step
        contract = load_contract_for_step("one-off")
        assert contract.instruction == "Local charter body.\n"
        assert contract.prompt_dir == str(prompt_dir.resolve())

    def test_skill_field_rejected(self, steps_dir):
        _write_dir_contract(steps_dir, "legacy-skill", {
            "id": "legacy-skill", "version": 1, "model": "sonnet", "skill": "explore",
        }, prompt_text=None)
        from orchestrator_next.parser import load_contract_for_step, ContractError
        with pytest.raises(ContractError, match="removed skill:"):
            load_contract_for_step("legacy-skill")

    def test_learnings_colocated_beside_prompt_dir(
        self, steps_dir, tmp_path, monkeypatch
    ):
        skills = tmp_path / "skills"
        prompt_dir = skills / "explore"
        prompt_dir.mkdir(parents=True)
        (prompt_dir / "SKILL.md").write_text("Body.\n")
        (prompt_dir / "learnings.md").write_text("- Prefer README scope.\n")
        monkeypatch.setenv("ORCHESTRATOR_SKILLS_TEST_OVERRIDE", str(skills))
        _write_dir_contract(steps_dir, "explore", {
            "id": "explore", "version": 1, "model": "sonnet", "prompt": "explore",
        }, prompt_text=None)

        from orchestrator_next.parser import load_contract_for_step
        contract = load_contract_for_step("explore")
        assert "Body." in contract.instruction
        assert "Prefer README scope." in contract.instruction
        # pack/learnings.md under the step dir must NOT be read
        pack = steps_dir / "explore" / "pack"
        pack.mkdir()
        (pack / "learnings.md").write_text("- Must not appear.\n")
        contract2 = load_contract_for_step("explore")
        assert "Must not appear." not in contract2.instruction


# ---------------------------------------------------------------------------
# TestScriptKindContractLoad: directory-form + script kind
# ---------------------------------------------------------------------------

class TestScriptKindContractLoad:
    """Tests for directory-form contract loading with kind: script.

    Class name carries 'script' into all pytest node IDs, so `-k script`
    selects all three scenarios in this class.
    """

    def test_script_dir_contract_loads_and_run_resolves_to_abs_path(self, steps_dir):
        """Scenario 1: directory form with script.sh loads; run resolves to absolute path.

        AC-2: given config/steps/<id>/contract.yaml (kind: script, run: script.sh)
        and sibling script.sh, load_contract_for_step returns a StepContract whose
        contract.run equals the absolute path <steps_dir>/<id>/script.sh.

        Currently RED: load_contract_for_step only looks for <id>.yaml, never
        <id>/contract.yaml, so it raises FileNotFoundError. Even if it did find
        the directory form, StepContract has no `kind` field and `run` would not
        be resolved to the absolute path.
        """
        step_dir = _write_dir_contract(
            steps_dir,
            "inline-step",
            {
                "id": "inline-step",
                "version": 1,
                "kind": "script",
                "run": "script.sh",
                "inputs": [],
                "outputs": [],
                "rules": [],
            },
            script_text="#!/bin/bash\necho 'expanding plan'\n",
        )
        expected_run = str(step_dir / "script.sh")

        from orchestrator_next.parser import load_contract_for_step, ScriptStepContract
        contract = load_contract_for_step("inline-step")
        assert isinstance(contract, ScriptStepContract)
        assert contract.run == expected_run

    def test_script_dir_contract_missing_script_raises_contract_dispatch_error(self, steps_dir):
        """Scenario 2: directory form with kind: script but missing script.sh raises ContractDispatchError.

        AC-6: load_contract_for_step must raise ContractDispatchError (not FileNotFoundError)
        when the step directory has contract.yaml but script.sh is absent.

        Currently RED: load_contract_for_step never enters the directory-form branch, so
        it raises FileNotFoundError (no <id>.yaml) instead of ContractDispatchError.
        """
        _write_dir_contract(
            steps_dir,
            "no-script",
            {
                "id": "no-script",
                "version": 1,
                "kind": "script",
                "run": "script.sh",
                "inputs": [],
                "outputs": [],
                "rules": [],
            },
            script_text=None,  # deliberately no script.sh
        )

        from orchestrator_next.parser import load_contract_for_step, ContractNotFoundError as ContractDispatchError
        with pytest.raises(ContractDispatchError, match="script"):
            load_contract_for_step("no-script")

    def test_dir_contract_missing_kind_raises_contract_error(self, steps_dir):
        """Agent contracts without prompt:/run: and without a sibling charter raise."""
        _write_dir_contract(
            steps_dir,
            "no-kind",
            {
                "id": "no-kind",
                "version": 1,
                # deliberately no 'kind' / prompt / run
                "agent": "architect",
                "inputs": [],
                "outputs": [],
                "rules": [],
            },
        )

        from orchestrator_next.parser import load_contract_for_step, ContractError
        with pytest.raises(ContractError, match="prompt: <dir>"):
            load_contract_for_step("no-kind")
