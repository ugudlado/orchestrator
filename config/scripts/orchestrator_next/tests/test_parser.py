"""
Tests for orchestrator_next.parser — StepContract.allowed_tools field.

T-1: RED tests — verify that allowed_tools field is parsed correctly.
These tests initially fail (ModuleNotFoundError / AttributeError on .allowed_tools)
until T-2 adds the field.
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
    """Create a temp steps directory and set env override."""
    d = tmp_path / "steps"
    d.mkdir()
    return d


@pytest.fixture(autouse=True)
def set_override(steps_dir, monkeypatch):
    """Point _load_contract to the temp steps dir."""
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(steps_dir))


def _write_contract(steps_dir, step_id: str, data: dict):
    (steps_dir / f"{step_id}.yaml").write_text(yaml.dump(data))


# ---------------------------------------------------------------------------
# T-1: StepContract.allowed_tools field
# ---------------------------------------------------------------------------

class TestStepContractAllowedTools:

    def test_allowed_tools_absent_gives_empty_list(self, steps_dir):
        """Contract without allowed_tools: -> allowed_tools == []."""
        _write_contract(steps_dir, "no-allowed", {
            "id": "no-allowed",
            "agent": "developer",
            "instruction": "do thing",
            "inputs": [],
            "outputs": [],
        })
        from orchestrator_next.parser import _load_contract
        contract = _load_contract("no-allowed", "")
        assert contract.allowed_tools == []

    def test_allowed_tools_null_gives_empty_list(self, steps_dir):
        """Contract with allowed_tools: null -> allowed_tools == []."""
        _write_contract(steps_dir, "null-allowed", {
            "id": "null-allowed",
            "agent": "developer",
            "instruction": "do thing",
            "inputs": [],
            "outputs": [],
            "allowed_tools": None,
        })
        from orchestrator_next.parser import _load_contract
        contract = _load_contract("null-allowed", "")
        assert contract.allowed_tools == []

    def test_allowed_tools_empty_list_gives_empty_list(self, steps_dir):
        """Contract with allowed_tools: [] -> allowed_tools == []."""
        _write_contract(steps_dir, "empty-allowed", {
            "id": "empty-allowed",
            "agent": "developer",
            "instruction": "do thing",
            "inputs": [],
            "outputs": [],
            "allowed_tools": [],
        })
        from orchestrator_next.parser import _load_contract
        contract = _load_contract("empty-allowed", "")
        assert contract.allowed_tools == []

    def test_allowed_tools_list_preserved(self, steps_dir):
        """Contract with allowed_tools: [Read, Grep] -> list preserved in declared order."""
        _write_contract(steps_dir, "with-allowed", {
            "id": "with-allowed",
            "agent": "developer",
            "instruction": "do thing",
            "inputs": [],
            "outputs": [],
            "allowed_tools": ["Read", "Grep"],
        })
        from orchestrator_next.parser import _load_contract
        contract = _load_contract("with-allowed", "")
        assert contract.allowed_tools == ["Read", "Grep"]

    def test_allowed_tools_field_exists_on_dataclass(self, steps_dir):
        """StepContract dataclass has allowed_tools attribute."""
        _write_contract(steps_dir, "check-field", {
            "id": "check-field",
            "agent": "developer",
            "instruction": "do thing",
            "inputs": [],
            "outputs": [],
        })
        from orchestrator_next.parser import _load_contract
        contract = _load_contract("check-field", "")
        # Should not raise AttributeError
        _ = contract.allowed_tools
