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
from pathlib import Path

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
def config_root(tmp_path: Path) -> Path:
    """A pack root; contracts live in its steps/ dir."""
    (tmp_path / "pack" / "steps").mkdir(parents=True)
    return tmp_path / "pack"


@pytest.fixture()
def steps_dir(config_root: Path) -> Path:
    """The pack's steps/ dir, where contract fixtures are written."""
    return config_root / "steps"


def _write_dir_contract(
    steps_dir: Path,
    step_id: str,
    contract_data: dict,
    prompt_text: str | None = None,
    script_text: str | None = None,
) -> Path:
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

    def test_agent_dir_contract_loads_kind_and_instruction(self, steps_dir, config_root):
        """Scenario 1: directory form with prompt.md loads kind=='agent' and instruction.

        AC-1: given config/steps/<id>/contract.yaml + prompt.md, load_contract_for_step
        returns StepContract with kind == 'agent' and instruction == prompt.md contents.

        Currently RED: load_contract_for_step only looks for <id>.yaml, never <id>/contract.yaml,
        so it raises FileNotFoundError (or fails AttributeError on .kind).
        """
        prompt_text = "You are the discoverer agent. Explore the codebase.\n"
        _write_dir_contract(
            steps_dir,
            "explore",
            {
                "id": "explore",
                "version": 1,
                "kind": "agent",
                "agent": "discoverer",
                "inputs": [],
                "outputs": ["discovery_result"],
                "rules": [],
            },
            prompt_text=prompt_text,
        )

        from orchestrator_next.parser import AgentStepContract, load_contract_for_step

        contract = load_contract_for_step("explore", config_root)
        assert isinstance(contract, AgentStepContract)
        assert contract.prompt_path == str((steps_dir / "explore" / "prompt.md").resolve())

    def test_agent_dir_contract_prefers_pack_prompt_md(self, steps_dir, config_root):
        """pack/prompt.md wins over a root prompt.md — steps-as-packs layout;
        root prompt.md remains as a fallback for unmigrated vendored configs."""
        step_dir = _write_dir_contract(
            steps_dir,
            "explore",
            {
                "id": "explore",
                "version": 1,
                "kind": "agent",
                "agent": "discoverer",
                "inputs": [],
                "outputs": ["discovery_result"],
                "rules": [],
            },
            prompt_text="Legacy root prompt.\n",
        )
        pack_dir = step_dir / "pack"
        pack_dir.mkdir()
        (pack_dir / "prompt.md").write_text("Pack prompt.\n")

        from orchestrator_next.parser import AgentStepContract, load_contract_for_step

        contract = load_contract_for_step("explore", config_root)
        assert isinstance(contract, AgentStepContract)
        assert contract.prompt_path == str((pack_dir / "prompt.md").resolve())

    def test_agent_dir_contract_missing_prompt_raises_contract_error(self, steps_dir, config_root):
        """Scenario 2: directory form with kind: agent but missing prompt.md raises ContractError.

        AC-6: load_contract_for_step must raise ContractError (not FileNotFoundError) when
        the step directory exists with contract.yaml but prompt.md is absent.

        Currently RED: load_contract_for_step never enters the directory-form branch, so
        it raises FileNotFoundError (no <id>.yaml) instead of ContractError.
        """
        _write_dir_contract(
            steps_dir,
            "no-prompt",
            {
                "id": "no-prompt",
                "version": 1,
                "kind": "agent",
                "agent": "discoverer",
                "inputs": [],
                "outputs": [],
                "rules": [],
            },
            prompt_text=None,
        )  # deliberately no prompt.md

        from orchestrator_next.parser import ContractError, load_contract_for_step

        with pytest.raises(ContractError, match="prompt: <path>.md"):
            load_contract_for_step("no-prompt", config_root)

    def test_prompt_dir_skill_md_preferred_and_frontmatter_stripped(self, steps_dir, config_root, tmp_path):
        """prompt: resolves an .md path; SKILL.md gets frontmatter stripped."""
        skill_dir = config_root.parent / "skills" / "explore"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: explore\ndescription: test\n---\n\nSkill body here.\n")
        _write_dir_contract(
            steps_dir,
            "explore",
            {
                "id": "explore",
                "version": 1,
                "prompt": "explore/SKILL.md",
            },
            prompt_text=None,
        )

        from orchestrator_next.parser import AgentStepContract, load_contract_for_step

        contract = load_contract_for_step("explore", config_root)
        assert isinstance(contract, AgentStepContract)
        assert contract.prompt_path == str((skill_dir / "SKILL.md").resolve())
        assert contract.prompt_dir == str(skill_dir.resolve())

    def test_step_local_skill_symlink_resolves_before_skills_search(self, steps_dir, config_root, tmp_path):
        """prompt: <id>/SKILL.md loads via step-dir symlink to pack-root skills/."""
        skill_dir = tmp_path / "elsewhere" / "explore"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: explore\n---\n\nFrom pack-root skill.\n")
        # The skills search would miss it: only the step-local symlink finds it.

        step_dir = _write_dir_contract(
            steps_dir,
            "explore",
            {
                "id": "explore",
                "version": 1,
                "prompt": "explore/SKILL.md",
            },
            prompt_text=None,
        )
        (step_dir / "explore").symlink_to(skill_dir)

        from orchestrator_next.parser import AgentStepContract, load_contract_for_step

        contract = load_contract_for_step("explore", config_root)
        assert isinstance(contract, AgentStepContract)
        assert contract.prompt_path == str((skill_dir / "SKILL.md").resolve())
        assert contract.prompt_dir == str(skill_dir.resolve())

    def test_prompt_field_loads_directory_with_prompt_md(self, steps_dir, config_root, tmp_path):
        prompt_dir = config_root.parent / "skills" / "one-off"
        prompt_dir.mkdir(parents=True)
        (prompt_dir / "prompt.md").write_text("Local charter body.\n")
        _write_dir_contract(
            steps_dir,
            "one-off",
            {
                "id": "one-off",
                "version": 1,
                "prompt": "one-off/prompt.md",
            },
            prompt_text=None,
        )

        from orchestrator_next.parser import AgentStepContract, load_contract_for_step

        contract = load_contract_for_step("one-off", config_root)
        assert isinstance(contract, AgentStepContract)
        assert contract.prompt_path == str((prompt_dir / "prompt.md").resolve())
        assert contract.prompt_dir == str(prompt_dir.resolve())

    def test_prompt_resolves_against_the_packs_own_skills_dir(self, steps_dir, config_root, tmp_path):
        """The search is one place: `<pack>/../skills`. No env, no override."""
        beside = config_root.parent / "skills" / "explore"
        beside.mkdir(parents=True)
        (beside / "SKILL.md").write_text("Body.\n")
        # A same-named dir somewhere else must NOT be found.
        stray = tmp_path / "stray" / "explore"
        stray.mkdir(parents=True)
        (stray / "SKILL.md").write_text("Wrong body.\n")
        _write_dir_contract(
            steps_dir,
            "explore",
            {
                "id": "explore",
                "version": 1,
                "prompt": "explore/SKILL.md",
            },
            prompt_text=None,
        )

        from orchestrator_next.parser import AgentStepContract, load_contract_for_step

        contract = load_contract_for_step("explore", config_root)
        assert isinstance(contract, AgentStepContract)
        assert contract.prompt_path == str((beside / "SKILL.md").resolve())
        assert contract.prompt_dir == str(beside.resolve())

    def test_contract_without_prompt_or_run_is_rejected(self, steps_dir, config_root):
        """A contract must name a payload. `skill:` was protocol v1's spelling
        and is no longer recognised, so a contract carrying only that is simply
        a contract with nothing to run."""
        _write_dir_contract(
            steps_dir,
            "no-payload",
            {
                "id": "no-payload",
                "version": 1,
                "skill": "explore",
            },
            prompt_text=None,
        )
        from orchestrator_next.parser import ContractError, load_contract_for_step

        with pytest.raises(ContractError) as exc:
            load_contract_for_step("no-payload", config_root)
        assert "must declare prompt:" in str(exc.value)


# ---------------------------------------------------------------------------
# TestScriptKindContractLoad: directory-form + script kind
# ---------------------------------------------------------------------------


class TestScriptKindContractLoad:
    """Tests for directory-form contract loading with kind: script.

    Class name carries 'script' into all pytest node IDs, so `-k script`
    selects all three scenarios in this class.
    """

    def test_script_dir_contract_loads_and_run_resolves_to_abs_path(self, steps_dir, config_root):
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

        from orchestrator_next.parser import ScriptStepContract, load_contract_for_step

        contract = load_contract_for_step("inline-step", config_root)
        assert isinstance(contract, ScriptStepContract)
        assert contract.run == expected_run

    def test_script_dir_contract_missing_script_raises_contract_dispatch_error(self, steps_dir, config_root):
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

        from orchestrator_next.parser import ContractNotFoundError as ContractDispatchError
        from orchestrator_next.parser import load_contract_for_step

        with pytest.raises(ContractDispatchError, match="script"):
            load_contract_for_step("no-script", config_root)

    def test_dir_contract_missing_kind_raises_contract_error(self, steps_dir, config_root):
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

        from orchestrator_next.parser import ContractError, load_contract_for_step

        with pytest.raises(ContractError, match="prompt: <path>.md"):
            load_contract_for_step("no-kind", config_root)


@pytest.mark.parametrize(
    "params, expected",
    [
        ({"COUNT": 3, "FLAG": False, 7: None}, {"COUNT": "3", "FLAG": "False", "7": "None"}),
        (["not", "a mapping"], {}),
        ("not a mapping", {}),
        (None, {}),
    ],
)
def test_script_params_are_loaded_as_strings(steps_dir, config_root, params, expected):
    from orchestrator_next.parser import ScriptStepContract, load_contract_for_step

    _write_dir_contract(steps_dir, "exec", {"run": "script.sh", "params": params}, script_text="exit 0\n")
    contract = load_contract_for_step("exec", config_root)
    assert isinstance(contract, ScriptStepContract)
    assert contract.params == expected
