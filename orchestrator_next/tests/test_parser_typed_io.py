"""
ORC-76 T-13: Failing tests for parser._load_contract typed inputs/outputs parsing.

These tests assert behavior that does not yet exist in parser.py. They are
intentionally RED — T-14 will make them pass by implementing typed I/O parsing
in _parse_contract_fields and StepContract.

Scenarios covered:
  1. Typed input {name: discovery, path: spec/changes/<slug>/discovery.md}
     parses to a typed spec with name and path attributes.
  2. Typed input {name: discovery, path: ..., optional: true} marks the
     input optional.
  3. Legacy string inputs (- discovery_result) still parse into the legacy
     named-handle form (name='discovery_result', path=None).
  4. Mixed list (one typed, one legacy) parses both items correctly.

AC-3 (design.md)

Why these tests are currently RED:
  _parse_contract_fields coerces all inputs items to str — typed dict items
  become str(item) (e.g. "{'name': 'discovery', 'path': '...'}"), not
  structured dicts. contract.inputs is list[str], so asserting
  contract.inputs[0]['name'] raises TypeError (can't subscript str).
  T-14 changes `inputs` to list[dict[str,Any]] and updates the parser.
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
    """Write a directory-form agent contract with a prompt.md sibling.

    Returns the step directory Path.
    """
    step_dir = steps_dir / step_id
    step_dir.mkdir(parents=True, exist_ok=True)
    (step_dir / "contract.yaml").write_text(yaml.dump(contract_data))
    (step_dir / "prompt.md").write_text(prompt_text)
    return step_dir


# ---------------------------------------------------------------------------
# Scenario 1: Typed input {name, path} parses to a typed spec
# ---------------------------------------------------------------------------

class TestTypedInputParsing:
    """Typed inputs/outputs produce structured dicts, not coerced strings."""

    def test_typed_input_has_name_and_path(self, steps_dir):
        """Scenario 1: {name: discovery, path: spec/changes/<slug>/discovery.md}
        parses to a dict with 'name' and 'path' keys.

        Currently RED: _parse_contract_fields coerces the dict to str(item),
        so contract.inputs[0] is the string representation of the dict, not
        a structured dict. Asserting ['name'] raises TypeError.
        """
        _write_dir_contract(steps_dir, "design-step", {
            "id": "design-step",
            "version": 1,
            "kind": "agent",
            "agent": "architect",
            "inputs": [
                {"name": "discovery", "path": "spec/changes/<slug>/discovery.md"},
            ],
            "outputs": [],
            "rules": [],
        })

        from orchestrator_next.parser import _load_contract
        contract = _load_contract("design-step", "")

        assert len(contract.inputs) == 1
        spec = contract.inputs[0]
        assert isinstance(spec, dict), (
            f"expected dict, got {type(spec).__name__!r}: {spec!r}"
        )
        assert spec["name"] == "discovery"
        assert spec["path"] == "spec/changes/<slug>/discovery.md"

    def test_typed_input_optional_false_by_default(self, steps_dir):
        """A typed input without optional: defaults to optional == False.

        Currently RED: same coercion issue as scenario 1.
        """
        _write_dir_contract(steps_dir, "design-step-opt-default", {
            "id": "design-step-opt-default",
            "version": 1,
            "kind": "agent",
            "agent": "architect",
            "inputs": [
                {"name": "discovery", "path": "spec/changes/<slug>/discovery.md"},
            ],
            "outputs": [],
            "rules": [],
        })

        from orchestrator_next.parser import _load_contract
        contract = _load_contract("design-step-opt-default", "")

        spec = contract.inputs[0]
        assert isinstance(spec, dict)
        # optional should be False (absent or explicitly False)
        assert spec.get("optional", False) is False


# ---------------------------------------------------------------------------
# Scenario 2: Typed input with optional: true marks the input optional
# ---------------------------------------------------------------------------

class TestTypedInputOptional:
    """optional: true on a typed input is preserved in the parsed spec."""

    def test_typed_input_optional_true_is_preserved(self, steps_dir):
        """Scenario 2: {name: tasks, path: ..., optional: true} sets optional=True.

        Currently RED: _parse_contract_fields coerces the dict to a string,
        losing the optional field entirely.
        """
        _write_dir_contract(steps_dir, "optional-input-step", {
            "id": "optional-input-step",
            "version": 1,
            "kind": "agent",
            "agent": "architect",
            "inputs": [
                {
                    "name": "tasks",
                    "path": "spec/changes/<slug>/tasks.yaml",
                    "optional": True,
                },
            ],
            "outputs": [],
            "rules": [],
        })

        from orchestrator_next.parser import _load_contract
        contract = _load_contract("optional-input-step", "")

        assert len(contract.inputs) == 1
        spec = contract.inputs[0]
        assert isinstance(spec, dict), (
            f"expected dict, got {type(spec).__name__!r}: {spec!r}"
        )
        assert spec["name"] == "tasks"
        assert spec["path"] == "spec/changes/<slug>/tasks.yaml"
        assert spec.get("optional") is True


# ---------------------------------------------------------------------------
# Scenario 3: Legacy string inputs parse into named-handle form
# ---------------------------------------------------------------------------

class TestLegacyStringInputs:
    """Legacy bare-string inputs remain loadable via the backward-compat path."""

    def test_legacy_string_input_has_name_and_no_path(self, steps_dir):
        """Scenario 3: '- discovery_result' parses to {name: 'discovery_result', path: None}.

        Currently RED: contract.inputs[0] is already the string 'discovery_result'
        (not a dict), so asserting ['name'] raises TypeError. T-14 will change
        all items to dicts, wrapping bare strings as {'name': item, 'path': None}.
        """
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
        assert spec.get("path") is None

    def test_legacy_optional_sugar_still_works(self, steps_dir):
        """Legacy {name: optional} sugar (ORC-63 AC-5) still marks input optional.

        Currently RED: contract.inputs[0] is a string, not a dict; after T-14
        it becomes {'name': 'discovery_result', 'path': None, 'optional': True}.
        """
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
        assert spec.get("path") is None
        assert spec.get("optional") is True


# ---------------------------------------------------------------------------
# Scenario 4: Mixed list (one typed, one legacy) parses both correctly
# ---------------------------------------------------------------------------

class TestMixedInputList:
    """A list containing both typed {name,path} specs and bare strings parses
    all items into the unified dict shape."""

    def test_mixed_inputs_parse_both_items(self, steps_dir):
        """Scenario 4: mixed list [typed_spec, legacy_string] parses both.

        Currently RED: _parse_contract_fields coerces the typed dict to str;
        contract.inputs[0] is a string representation, not a dict.
        """
        _write_dir_contract(steps_dir, "mixed-input-step", {
            "id": "mixed-input-step",
            "version": 1,
            "kind": "agent",
            "agent": "architect",
            "inputs": [
                {"name": "discovery", "path": "spec/changes/<slug>/discovery.md"},
                "prior_result",
            ],
            "outputs": [],
            "rules": [],
        })

        from orchestrator_next.parser import _load_contract
        contract = _load_contract("mixed-input-step", "")

        assert len(contract.inputs) == 2

        typed_spec = contract.inputs[0]
        assert isinstance(typed_spec, dict), (
            f"expected dict for typed spec, got {type(typed_spec).__name__!r}: {typed_spec!r}"
        )
        assert typed_spec["name"] == "discovery"
        assert typed_spec["path"] == "spec/changes/<slug>/discovery.md"

        legacy_spec = contract.inputs[1]
        assert isinstance(legacy_spec, dict), (
            f"expected dict for legacy spec, got {type(legacy_spec).__name__!r}: {legacy_spec!r}"
        )
        assert legacy_spec["name"] == "prior_result"
        assert legacy_spec.get("path") is None

    def test_mixed_inputs_optional_field_only_on_optional_items(self, steps_dir):
        """Mixed list: optional: true appears only on items that declare it.

        Currently RED: same coercion failure.
        """
        _write_dir_contract(steps_dir, "mixed-opt-step", {
            "id": "mixed-opt-step",
            "version": 1,
            "kind": "agent",
            "agent": "architect",
            "inputs": [
                {"name": "discovery", "path": "spec/changes/<slug>/discovery.md"},
                {
                    "name": "tasks",
                    "path": "spec/changes/<slug>/tasks.yaml",
                    "optional": True,
                },
            ],
            "outputs": [],
            "rules": [],
        })

        from orchestrator_next.parser import _load_contract
        contract = _load_contract("mixed-opt-step", "")

        assert len(contract.inputs) == 2

        required = contract.inputs[0]
        assert isinstance(required, dict)
        assert required["name"] == "discovery"
        assert required.get("optional", False) is False

        optional = contract.inputs[1]
        assert isinstance(optional, dict)
        assert optional["name"] == "tasks"
        assert optional.get("optional") is True
