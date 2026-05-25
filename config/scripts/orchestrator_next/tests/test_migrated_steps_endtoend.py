"""
ORC-76 T-10: End-to-end tests for all migrated step directory contracts.

Discovers every config/steps/<id>/ directory that contains a contract.yaml
and asserts:
  - contract.kind matches the 'kind:' declared in contract.yaml
  - For kind == 'agent': contract.instruction is non-empty (loaded from prompt.md)
  - For kind == 'script': the resolved run path exists on disk

These tests are GREEN — they validate the already-migrated contracts.

AC-1, AC-2, AC-8 (design.md)
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

# The repo root is four levels up from this file:
# config/scripts/orchestrator_next/tests/ -> config/scripts/orchestrator_next/ ->
# config/scripts/ -> config/ -> <repo_root>
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))
_STEPS_DIR = os.path.join(_REPO_ROOT, "config", "steps")


def _discover_step_dirs() -> list[str]:
    """Return sorted list of step IDs that have a contract.yaml in config/steps/<id>/."""
    step_ids = []
    for entry in os.scandir(_STEPS_DIR):
        if not entry.is_dir():
            continue
        contract_path = os.path.join(entry.path, "contract.yaml")
        if os.path.isfile(contract_path):
            step_ids.append(entry.name)
    return sorted(step_ids)


def _step_kind(step_id: str) -> str:
    """Read the kind field from a step's contract.yaml."""
    contract_yaml_path = os.path.join(_STEPS_DIR, step_id, "contract.yaml")
    with open(contract_yaml_path) as f:
        return yaml.safe_load(f).get("kind", "")


_ALL_STEP_IDS = _discover_step_dirs()
_AGENT_STEP_IDS = [sid for sid in _ALL_STEP_IDS if _step_kind(sid) == "agent"]
_SCRIPT_STEP_IDS = [sid for sid in _ALL_STEP_IDS if _step_kind(sid) == "script"]


@pytest.fixture(autouse=True)
def point_parser_at_real_steps(monkeypatch):
    """Point _load_contract at the real config/steps/ directory."""
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", _STEPS_DIR)


@pytest.mark.parametrize("step_id", _ALL_STEP_IDS)
def test_contract_kind_matches_yaml(step_id: str):
    """For every migrated step dir, parser._load_contract returns kind matching contract.yaml."""
    expected_kind = _step_kind(step_id)
    assert expected_kind in ("agent", "script"), (
        f"{step_id}/contract.yaml declares kind={expected_kind!r}; must be 'agent' or 'script'"
    )

    from orchestrator_next.parser import _load_contract
    contract = _load_contract(step_id, "")

    assert contract.kind == expected_kind, (
        f"{step_id}: expected kind={expected_kind!r}, got kind={contract.kind!r}"
    )


@pytest.mark.parametrize("step_id", _AGENT_STEP_IDS)
def test_agent_instruction_non_empty(step_id: str):
    """For every agent step, contract.instruction is non-empty (loaded from prompt.md)."""
    from orchestrator_next.parser import _load_contract
    contract = _load_contract(step_id, "")

    assert contract.instruction, (
        f"{step_id}: contract.instruction is empty; prompt.md may be missing or blank"
    )
    # Confirm the prompt.md source file is non-empty
    prompt_path = os.path.join(_STEPS_DIR, step_id, "prompt.md")
    assert os.path.isfile(prompt_path), f"{step_id}: prompt.md not found at {prompt_path}"
    assert os.path.getsize(prompt_path) > 0, f"{step_id}: prompt.md is empty"


@pytest.mark.parametrize("step_id", _SCRIPT_STEP_IDS)
def test_script_run_path_exists(step_id: str):
    """For every script step, the resolved run path exists on disk and is readable."""
    from orchestrator_next.parser import _load_contract
    contract = _load_contract(step_id, "")

    assert contract.run, (
        f"{step_id}: contract.run is empty after loading script contract"
    )
    assert os.path.isfile(contract.run), (
        f"{step_id}: resolved run path does not exist: {contract.run}"
    )
    assert os.access(contract.run, os.R_OK), (
        f"{step_id}: resolved run path is not readable: {contract.run}"
    )
