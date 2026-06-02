"""
ORC-76 T-4: Failing tests for generate_plan._load_step_contract_raw directory-form lookup.

These tests assert behavior that does not yet exist in generate_plan.py. They are
intentionally RED — T-5 will make them pass by implementing directory-form lookup
in _load_step_contract_raw.

Scenarios covered:
  1. _load_step_contract_raw returns the dict from <id>/contract.yaml when the
     directory form exists (directory preferred over flat file).
  2. Falls back to <id>.yaml when only the flat form exists (back-compat).
  3. _build_step_block reads rules from the directory form's contract.yaml.

AC-1, AC-7 (design.md)
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
    """Point _load_step_contract_raw to the temp steps dir via env override."""
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(steps_dir))


def _write_flat_contract(steps_dir, step_id: str, data: dict) -> None:
    """Write a flat-file contract (legacy form)."""
    (steps_dir / f"{step_id}.yaml").write_text(yaml.dump(data))


def _write_dir_contract(steps_dir, step_id: str, contract_data: dict) -> None:
    """Write a directory-form contract (new form, no payload files needed for raw load)."""
    step_dir = steps_dir / step_id
    step_dir.mkdir(parents=True, exist_ok=True)
    (step_dir / "contract.yaml").write_text(yaml.dump(contract_data))


# ---------------------------------------------------------------------------
# Scenario 1: directory form found → return dict from <id>/contract.yaml
# ---------------------------------------------------------------------------


def test_load_step_contract_raw_prefers_directory_form(steps_dir):
    """_load_step_contract_raw returns dict from <id>/contract.yaml when directory form exists.

    AC-1: the directory form must be preferred over the flat file.

    Currently RED: _load_step_contract_raw only looks for <id>.yaml, never
    <id>/contract.yaml, so it returns None (directory form ignored).
    """
    contract_data = {
        "id": "explore",
        "version": 1,
        "kind": "agent",
        "agent": "discoverer",
        "inputs": [],
        "outputs": ["discovery_result"],
        "rules": ["Always cite the file path when referencing code."],
    }
    _write_dir_contract(steps_dir, "explore", contract_data)

    from orchestrator_next.generate_plan import _load_step_contract_raw

    result = _load_step_contract_raw("explore", "")

    assert result is not None, "_load_step_contract_raw returned None; expected dict from directory form"
    assert result.get("kind") == "agent", f"Expected kind='agent', got {result.get('kind')!r}"
    assert result.get("rules") == ["Always cite the file path when referencing code."]


def test_load_step_contract_raw_directory_preferred_over_flat(steps_dir):
    """When both directory and flat forms exist, the directory form wins.

    Currently RED: _load_step_contract_raw only searches for <id>.yaml,
    so it returns the flat-file dict rather than the directory form.
    """
    flat_data = {
        "id": "explore",
        "version": 1,
        "agent": "old-agent",
        "rules": ["old rule from flat file"],
    }
    dir_data = {
        "id": "explore",
        "version": 1,
        "kind": "agent",
        "agent": "new-agent",
        "rules": ["new rule from directory form"],
    }
    _write_flat_contract(steps_dir, "explore", flat_data)
    _write_dir_contract(steps_dir, "explore", dir_data)

    from orchestrator_next.generate_plan import _load_step_contract_raw

    result = _load_step_contract_raw("explore", "")

    assert result is not None
    assert result.get("agent") == "new-agent", (
        f"Expected 'new-agent' from directory form, got {result.get('agent')!r}"
    )
    assert result.get("rules") == ["new rule from directory form"]


# ---------------------------------------------------------------------------
# Scenario 2: fallback to <id>.yaml when only flat form exists (back-compat)
# ---------------------------------------------------------------------------


def test_load_step_contract_raw_falls_back_to_flat_file(steps_dir):
    """Falls back to <id>.yaml when only the flat form exists.

    AC-7: back-compat read path must still work for flat-file contracts.

    Currently GREEN: the existing implementation already reads <id>.yaml.
    This test must pass before T-5 and must continue to pass after T-5.
    (Included here so the test file covers the full scenario set for T-4.)
    """
    flat_data = {
        "id": "diagnose",
        "agent": "architect",
        "instruction": "Diagnose the issue carefully.",
        "inputs": [],
        "outputs": ["diagnosis"],
        "rules": ["back-compat rule"],
    }
    _write_flat_contract(steps_dir, "diagnose", flat_data)

    from orchestrator_next.generate_plan import _load_step_contract_raw

    result = _load_step_contract_raw("diagnose", "")

    assert result is not None, "_load_step_contract_raw returned None for flat form"
    assert result.get("agent") == "architect"
    assert result.get("rules") == ["back-compat rule"]


# ---------------------------------------------------------------------------
# Scenario 3: _build_step_block reads rules from directory form
# ---------------------------------------------------------------------------


def test_build_step_block_reads_rules_from_directory_form(steps_dir, tmp_path, monkeypatch):
    """_build_step_block picks up rules from the directory-form contract.yaml.

    AC-1: rule merge must draw from the directory-form dict.

    Currently RED: _build_step_block calls _load_step_contract_raw, which only
    finds <id>.yaml. With only a directory-form contract present, it falls back
    to None, so no rules reach the block.
    """
    contract_data = {
        "id": "design-and-draft-artifacts",
        "version": 1,
        "kind": "agent",
        "agent": "architect",
        "inputs": ["discovery_result"],
        "outputs": ["design_result"],
        "rules": ["dir-form rule: always output a tasks.yaml"],
    }
    _write_dir_contract(steps_dir, "design-and-draft-artifacts", contract_data)

    # Minimal supporting files for _build_step_block
    home = tmp_path / "orchestrator_home"
    workflows_dir = home / "config" / "workflows"
    workflows_dir.mkdir(parents=True)
    schema = {
        "name": "feature",
        "version": 1,
        "rules": [],
        "phases": [
            {
                "name": "specify",
                "goal": "Produce spec.",
                "rules": [],
                "steps": ["design-and-draft-artifacts"],
            }
        ],
    }
    (workflows_dir / "feature.yaml").write_text(yaml.safe_dump(schema))

    project = {
        "version": 1,
        "project": {"name": "test-repo", "repo": "test-repo", "summary": ""},
        "rules": [],
        "verify_commands": {"test": "pytest"},
    }
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir(parents=True)
    (spec_dir / "project.yaml").write_text(yaml.safe_dump(project))

    monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))

    phase_def = schema["phases"][0]
    repo_name = "test-repo"

    from orchestrator_next.generate_plan import _build_step_block

    block = _build_step_block(
        "design-and-draft-artifacts",
        phase_def,
        schema,
        project,
        repo_name,
        "",
    )

    rules_in_block = block.get("rules", [])
    assert any("dir-form rule" in str(r) for r in rules_in_block), (
        f"Expected directory-form rule in block rules, got: {rules_in_block}"
    )
