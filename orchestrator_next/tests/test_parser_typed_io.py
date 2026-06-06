"""
Tests for parser._load_contract input parsing.

Scenarios covered:
  1. Legacy string inputs (- discovery_result) parse into {name, optional: False}.
  2. Optional sugar {<name>: optional} marks the input optional.
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


def _write_dir_contract(
    steps_dir,
    step_id: str,
    contract_data: dict,
    prompt_text: str = "Agent instruction text.\n",
) -> object:
    """Write a directory-form agent contract with a prompt.md sibling."""
    step_dir = steps_dir / step_id
    step_dir.mkdir(parents=True, exist_ok=True)
    (step_dir / "contract.yaml").write_text(yaml.dump(contract_data))
    (step_dir / "prompt.md").write_text(prompt_text)
    return step_dir


# ---------------------------------------------------------------------------
# Scenario 1: Legacy string inputs parse into named-handle form
# ---------------------------------------------------------------------------

class TestLegacyStringInputs:
    """Legacy bare-string inputs remain loadable."""

    def test_legacy_string_input_has_name(self, steps_dir):
        """'- discovery_result' parses to {name: 'discovery_result', optional: False}."""
        _write_dir_contract(steps_dir, "legacy-input-step", {
            "id": "legacy-input-step",
            "version": 1,
            "kind": "agent",
            "agent": "architect",
            "inputs": ["discovery_result"],
            "outputs": [],
            "rules": [],
        })

        from orchestrator_next.parser import _load_contract
        contract = _load_contract("legacy-input-step", "")

        assert len(contract.inputs) == 1
        spec = contract.inputs[0]
        assert isinstance(spec, dict), (
            f"expected dict, got {type(spec).__name__!r}: {spec!r}"
        )
        assert spec["name"] == "discovery_result"
        assert spec.get("optional", False) is False

    def test_legacy_optional_sugar_still_works(self, steps_dir):
        """Legacy {name: optional} sugar marks input optional."""
        _write_dir_contract(steps_dir, "legacy-optional-step", {
            "id": "legacy-optional-step",
            "version": 1,
            "kind": "agent",
            "agent": "architect",
            "inputs": [{"discovery_result": "optional"}],
            "outputs": [],
            "rules": [],
        })

        from orchestrator_next.parser import _load_contract
        contract = _load_contract("legacy-optional-step", "")

        assert len(contract.inputs) == 1
        spec = contract.inputs[0]
        assert isinstance(spec, dict)
        assert spec["name"] == "discovery_result"
        assert spec.get("optional") is True
