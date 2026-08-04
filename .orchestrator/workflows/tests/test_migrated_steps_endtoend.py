"""
  - prompt steps (prompt:) load non-empty instruction + prompt_dir
  - shell steps (run:) resolve to an existing script path
"""
from __future__ import annotations

import os
import sys

import pytest
import yaml

from orchestrator_next.parser import AgentStepContract, ScriptStepContract, load_contract_for_step

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_SCRIPTS_DIR = _REPO_ROOT
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

_STEPS_DIR = os.path.join(_REPO_ROOT, "config", "steps")
_SKILLS_DIR = os.path.join(_REPO_ROOT, "skills")


def _discover_step_dirs() -> list[str]:
    step_ids = []
    for entry in os.scandir(_STEPS_DIR):
        if not entry.is_dir():
            continue
        contract_path = os.path.join(entry.path, "contract.yaml")
        if os.path.isfile(contract_path):
            step_ids.append(entry.name)
    return sorted(step_ids)


def _step_kind(step_id: str) -> str:
    contract_yaml_path = os.path.join(_STEPS_DIR, step_id, "contract.yaml")
    with open(contract_yaml_path) as f:
        data = yaml.safe_load(f) or {}
    if data.get("run"):
        return "shell"
    if data.get("prompt"):
        return "prompt"
    return "unknown"


_ALL_STEP_IDS = _discover_step_dirs()
_AGENT_STEP_IDS = [sid for sid in _ALL_STEP_IDS if _step_kind(sid) == "prompt"]
_SHELL_STEP_IDS = [sid for sid in _ALL_STEP_IDS if _step_kind(sid) == "shell"]


@pytest.fixture(autouse=True)
def point_parser_at_real_steps(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", _STEPS_DIR)
    monkeypatch.setenv("ORCHESTRATOR_SKILLS_TEST_OVERRIDE", _SKILLS_DIR)


@pytest.mark.parametrize("step_id", _ALL_STEP_IDS)
def test_contract_kind_matches_yaml(step_id: str):
    expected_kind = _step_kind(step_id)
    assert expected_kind in ("prompt", "shell"), (
        f"{step_id}/contract.yaml must declare run: | prompt:; got {expected_kind!r}"
    )

    contract = load_contract_for_step(step_id)
    if expected_kind == "prompt":
        assert isinstance(contract, AgentStepContract), (
            f"{step_id}: expected AgentStepContract, got {type(contract).__name__}"
        )
    else:
        assert isinstance(contract, ScriptStepContract), (
            f"{step_id}: expected ScriptStepContract, got {type(contract).__name__}"
        )


@pytest.mark.parametrize("step_id", _AGENT_STEP_IDS)
def test_agent_instruction_non_empty(step_id: str):
    contract = load_contract_for_step(step_id)
    assert isinstance(contract, AgentStepContract)
    assert contract.instruction, (
        f"{step_id}: contract.instruction is empty"
    )
    assert contract.prompt_dir and os.path.isdir(contract.prompt_dir), (
        f"{step_id}: missing prompt_dir {contract.prompt_dir!r}"
    )
    with open(os.path.join(_STEPS_DIR, step_id, "contract.yaml")) as f:
        data = yaml.safe_load(f) or {}
    assert data.get("prompt"), f"{step_id}: expected prompt: in contract"


@pytest.mark.parametrize("step_id", _SHELL_STEP_IDS)
def test_shell_run_path_exists(step_id: str):
    contract = load_contract_for_step(step_id)
    assert isinstance(contract, ScriptStepContract)
    assert contract.run and os.path.isfile(contract.run)
    assert os.access(contract.run, os.R_OK)
