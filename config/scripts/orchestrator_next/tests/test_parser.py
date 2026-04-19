"""
Tests for orchestrator_next.parser — StepContract.allowed_tools field and
State.complexity field with load_state() validation.

T-1 (original): RED tests for allowed_tools field.
T-1 (HL-291): RED tests for State.complexity field and load_state() validation.
"""
from __future__ import annotations

import io
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


# ---------------------------------------------------------------------------
# T-1 (HL-291): State.complexity field and load_state() validation
# ---------------------------------------------------------------------------

class TestStateComplexityField:
    """Tests for State.complexity field and load_state() validation (FR-1, FR-2)."""

    def _write_state(self, tmp_path, data: dict):
        """Write a minimal state.yaml with given data and return path."""
        p = tmp_path / "state.yaml"
        p.write_text(yaml.dump(data))
        return str(p)

    def test_complexity_field_exists_on_state_dataclass(self):
        """State dataclass must expose a complexity field (FR-1)."""
        from orchestrator_next.parser import State
        assert "complexity" in State.__dataclass_fields__

    def test_complexity_defaults_to_none(self):
        """State.complexity default value is None (FR-1)."""
        from orchestrator_next.parser import State
        assert State.__dataclass_fields__["complexity"].default is None

    def test_complexity_populated_from_state_yaml(self, tmp_path):
        """load_state() populates State.complexity from state.yaml (FR-1)."""
        p = self._write_state(tmp_path, {
            "change_id": "my-feature",
            "phase": "implement",
            "complexity": "M",
            "workflow_plan": {},
            "step_history": [],
        })
        from orchestrator_next.parser import load_state
        state = load_state(p)
        assert state.complexity == "M"

    def test_complexity_none_when_absent_from_state_yaml(self, tmp_path):
        """State.complexity is None when complexity: key absent from state.yaml (FR-1, NFR-1)."""
        p = self._write_state(tmp_path, {
            "change_id": "my-feature",
            "phase": "implement",
            "workflow_plan": {},
            "step_history": [],
        })
        from orchestrator_next.parser import load_state
        state = load_state(p)
        assert state.complexity is None

    @pytest.mark.parametrize("value", ["XS", "S", "M", "L", "XL"])
    def test_valid_complexity_values_accepted(self, tmp_path, value):
        """All valid complexity values {XS,S,M,L,XL} are accepted without warning (FR-2)."""
        p = self._write_state(tmp_path, {
            "change_id": "my-feature",
            "phase": "implement",
            "complexity": value,
            "workflow_plan": {},
            "step_history": [],
        })
        from orchestrator_next.parser import load_state
        state = load_state(p)
        assert state.complexity == value

    def test_invalid_complexity_coerced_to_none(self, tmp_path, capsys):
        """Unknown complexity value is coerced to None (FR-2, AC-4)."""
        p = self._write_state(tmp_path, {
            "change_id": "my-feature",
            "phase": "implement",
            "complexity": "XXXX",
            "workflow_plan": {},
            "step_history": [],
        })
        from orchestrator_next.parser import load_state
        state = load_state(p)
        assert state.complexity is None

    def test_invalid_complexity_emits_stderr_warning(self, tmp_path, capsys):
        """Unknown complexity value emits a stderr warning line (FR-2, AC-4)."""
        p = self._write_state(tmp_path, {
            "change_id": "my-feature",
            "phase": "implement",
            "complexity": "XXXX",
            "workflow_plan": {},
            "step_history": [],
        })
        from orchestrator_next.parser import load_state
        load_state(p)
        captured = capsys.readouterr()
        assert "[complexity]" in captured.err
        assert "XXXX" in captured.err

    def test_invalid_complexity_does_not_raise(self, tmp_path):
        """Unknown complexity value must not raise (FR-2, AC-4)."""
        p = self._write_state(tmp_path, {
            "change_id": "my-feature",
            "phase": "implement",
            "complexity": "GIGANTIC",
            "workflow_plan": {},
            "step_history": [],
        })
        from orchestrator_next.parser import load_state
        # Must not raise
        state = load_state(p)
        assert state.complexity is None

    def test_warning_includes_change_id(self, tmp_path, capsys):
        """Stderr warning includes the change_id for identification (FR-2)."""
        p = self._write_state(tmp_path, {
            "change_id": "my-feature",
            "phase": "implement",
            "complexity": "XXXX",
            "workflow_plan": {},
            "step_history": [],
        })
        from orchestrator_next.parser import load_state
        load_state(p)
        captured = capsys.readouterr()
        assert "my-feature" in captured.err
