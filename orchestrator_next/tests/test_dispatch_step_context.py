"""
Tests for dispatch.py step_context injection (ORC-63: built from the node
dict in workflow_plan.nodes — plan.yaml eliminated).

  test_run_inline_has_step_context
  test_run_step_has_step_context
  test_verify_phase_omits_step_context
  test_step_context_built_from_node
  test_legacy_active_block_still_dispatches
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_contract(contracts_dir: Path, step_id: str, data: dict) -> None:
    (contracts_dir / f"{step_id}.yaml").write_text(yaml.safe_dump(data))


def _node(step_id: str, status: str = "pending", **extra) -> dict:
    node = {
        "id": step_id,
        "status": status,
        "agent": "developer",
        "goal": "Test phase goal.",
        "inputs": [],
        "outputs": ["result"],
        "rules": ["Keep it scoped."],
    }
    node.update(extra)
    return node


def _make_state_yaml(state_dir: Path, phase: str, nodes: list[dict]) -> str:
    """Write a state.yaml with the ORC-63 nodes-shape workflow_plan."""
    state = {
        "change_id": "test-feature",
        "slug": "test-feature",
        "schema": "feature",
        "status": "active",
        "repo_root": str(state_dir),
        "workflow_plan": {phase: {"nodes": nodes, "filtered": []}},
        "phase": phase,
        "step_history": [],
    }
    path = state_dir / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_run_inline_has_step_context(tmp_path, monkeypatch):
    """An inline run action carries step_context built from the node."""
    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir()
    _write_contract(contracts_dir, "my-inline-step", {
        "id": "my-inline-step",
        "agent": "developer",
        "run": "scripts/run.sh",
        "instruction": "do inline thing",
        "inputs": [],
        "outputs": [],
        "rules": [],
    })
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts_dir))

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    sp = _make_state_yaml(state_dir, "implement", [_node("my-inline-step")])

    from orchestrator_next.dispatch import dispatch
    from orchestrator_next.parser import load_state
    action, code = dispatch(load_state(sp), sp)

    assert code == 0
    assert "step_context" in action, "action must include step_context (ORC-45)"
    ctx = action["step_context"]
    assert ctx["id"] == "my-inline-step"
    assert "rules" in ctx
    assert "goal" in ctx


def test_run_step_has_step_context(tmp_path, monkeypatch):
    """An agent run action carries step_context built from the node."""
    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir()
    _write_contract(contracts_dir, "my-run-step", {
        "id": "my-run-step",
        "agent": "developer",
        "run": "scripts/run.sh",
        "instruction": "run it",
        "inputs": [],
        "outputs": [],
        "rules": [],
    })
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts_dir))

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    sp = _make_state_yaml(state_dir, "implement", [_node("my-run-step")])

    from orchestrator_next.dispatch import dispatch
    from orchestrator_next.parser import load_state
    action, code = dispatch(load_state(sp), sp)

    assert code == 0
    assert "step_context" in action
    ctx = action["step_context"]
    assert ctx["id"] == "my-run-step"
    assert ctx["rules"] == ["Keep it scoped."]


def test_verify_phase_omits_step_context(tmp_path, monkeypatch):
    """When the phase is complete, dispatch exits 1 with an empty action."""
    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts_dir))

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    # The only node is already completed → no ready node → exit 1.
    sp = _make_state_yaml(state_dir, "specify", [_node("done-step", status="completed")])

    from orchestrator_next.dispatch import dispatch
    from orchestrator_next.parser import load_state
    action, code = dispatch(load_state(sp), sp)
    assert code == 1, f"Expected exit 1 (phase complete), got {code}"
    assert "step_context" not in action


def test_step_context_built_from_node(tmp_path, monkeypatch):
    """step_context reflects the chosen node's fields verbatim (not plan.yaml)."""
    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir()
    _write_contract(contracts_dir, "a", {
        "id": "a", "agent": "developer", "instruction": "x",
        "inputs": [], "outputs": [], "rules": [],
    })
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts_dir))

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    node = _node("a", goal="Special goal.", depends_on=[])
    sp = _make_state_yaml(state_dir, "implement", [node])

    from orchestrator_next.dispatch import dispatch
    from orchestrator_next.parser import load_state
    action, code = dispatch(load_state(sp), sp)
    assert code == 0
    assert action["step_context"]["goal"] == "Special goal."


def test_legacy_active_block_still_dispatches(tmp_path, monkeypatch):
    """AC-11: a legacy active:[ids] block still dispatches without a migration."""
    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir()
    _write_contract(contracts_dir, "legacy-step", {
        "id": "legacy-step", "agent": "developer", "instruction": "x",
        "inputs": [], "outputs": [], "rules": [],
    })
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts_dir))

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    state = {
        "change_id": "test-feature",
        "phase": "implement",
        "repo_root": str(state_dir),
        "workflow_plan": {"implement": {"active": ["legacy-step"], "filtered": []}},
        "step_history": [],
    }
    path = state_dir / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))

    from orchestrator_next.dispatch import dispatch
    from orchestrator_next.parser import load_state
    action, code = dispatch(load_state(str(path)), str(path))
    assert code == 0
    assert action["step_id"] == "legacy-step"
