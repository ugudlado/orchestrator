"""
ORC-76 T-1: Failing tests for parser._load_contract directory-form loading (agent kind).

These tests assert behavior that does not yet exist in parser.py. They are
intentionally RED — T-3 will make them pass by implementing directory-form
loading in _load_contract.

Scenarios covered:
  1. Directory <id>/contract.yaml with sibling prompt.md loads:
     kind == 'agent', instruction == prompt.md contents.
  2. Directory <id>/contract.yaml with kind: agent but missing prompt.md
     raises ContractError.
  3. Flat <id>.yaml still loads (back-compat) and synthesizes kind from
     absence of run:.

AC-1, AC-6, AC-7 (design.md)
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
    """Point _load_contract to the temp steps dir."""
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(steps_dir))


def _write_flat_contract(steps_dir, step_id: str, data: dict):
    """Write a flat-file contract (legacy form)."""
    (steps_dir / f"{step_id}.yaml").write_text(yaml.dump(data))


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
    """Tests for directory-form contract loading with kind: agent.

    Class name carries 'agent' into all pytest node IDs, so `-k agent` selects
    all three scenarios in this class.
    """

    def test_agent_dir_contract_loads_kind_and_instruction(self, steps_dir):
        """Scenario 1: directory form with prompt.md loads kind=='agent' and instruction.

        AC-1: given config/steps/<id>/contract.yaml + prompt.md, _load_contract
        returns StepContract with kind == 'agent' and instruction == prompt.md contents.

        Currently RED: _load_contract only looks for <id>.yaml, never <id>/contract.yaml,
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

        from orchestrator_next.parser import _load_contract
        contract = _load_contract("explore", "")
        assert contract.kind == "agent"
        assert contract.instruction == prompt_text

    def test_agent_dir_contract_missing_prompt_raises_contract_error(self, steps_dir):
        """Scenario 2: directory form with kind: agent but missing prompt.md raises ContractError.

        AC-6: _load_contract must raise ContractError (not FileNotFoundError) when
        the step directory exists with contract.yaml but prompt.md is absent.

        Currently RED: _load_contract never enters the directory-form branch, so
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

        from orchestrator_next.parser import _load_contract, ContractError
        with pytest.raises(ContractError, match="missing prompt.md"):
            _load_contract("no-prompt", "")

    def test_agent_flat_file_back_compat_synthesizes_kind(self, steps_dir):
        """Scenario 3: flat-file form without run: synthesizes kind == 'agent'.

        AC-7: the back-compat read path must set kind = 'agent' when the flat
        contract has no run: field.

        Currently RED: StepContract has no `kind` field, so accessing .kind raises
        AttributeError.
        """
        _write_flat_contract(steps_dir, "diagnose", {
            "id": "diagnose",
            "agent": "architect",
            "instruction": "Diagnose the issue carefully.",
            "inputs": [],
            "outputs": ["diagnosis"],
            "rules": [],
        })

        from orchestrator_next.parser import _load_contract
        contract = _load_contract("diagnose", "")
        assert contract.kind == "agent"


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
        and sibling script.sh, _load_contract returns a StepContract whose
        contract.run equals the absolute path <steps_dir>/<id>/script.sh.

        Currently RED: _load_contract only looks for <id>.yaml, never
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

        from orchestrator_next.parser import _load_contract
        contract = _load_contract("inline-step", "")
        assert contract.kind == "script"
        assert contract.run == expected_run

    def test_script_dir_contract_missing_script_raises_contract_dispatch_error(self, steps_dir):
        """Scenario 2: directory form with kind: script but missing script.sh raises ContractDispatchError.

        AC-6: _load_contract must raise ContractDispatchError (not FileNotFoundError)
        when the step directory has contract.yaml but script.sh is absent.

        Currently RED: _load_contract never enters the directory-form branch, so
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

        from orchestrator_next.parser import _load_contract, ContractDispatchError
        with pytest.raises(ContractDispatchError, match="script"):
            _load_contract("no-script", "")

    def test_dir_contract_missing_kind_raises_contract_error(self, steps_dir):
        """Scenario 3: contract.yaml missing kind: field raises ContractError naming the field.

        Design.md error table: _load_contract raises
        ContractError("contract <id> missing kind: field (agent|script)")
        when kind is absent from a directory-form contract.yaml.

        Currently RED: _load_contract never reads contract.yaml, so it raises
        FileNotFoundError (no <id>.yaml) instead of ContractError.
        """
        _write_dir_contract(
            steps_dir,
            "no-kind",
            {
                "id": "no-kind",
                "version": 1,
                # deliberately no 'kind' field
                "agent": "architect",
                "inputs": [],
                "outputs": [],
                "rules": [],
            },
        )

        from orchestrator_next.parser import _load_contract, ContractError
        with pytest.raises(ContractError, match="kind"):
            _load_contract("no-kind", "")
