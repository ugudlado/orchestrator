"""
Tests for dispatch.py step_context injection.

Five tests per design.md § Testing Strategy:
  test_run_inline_has_step_context
  test_run_step_has_step_context
  test_verify_phase_omits_step_context
  test_missing_plan_yaml_exits_3
  test_step_missing_in_plan_exits_3
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
# Helpers
# ---------------------------------------------------------------------------


def _write_contract(contracts_dir: Path, step_id: str, data: dict) -> None:
    (contracts_dir / f"{step_id}.yaml").write_text(yaml.safe_dump(data))


def _write_plan_yaml(state_dir: Path, phase: str, step_ids: list[str]) -> None:
    """Write a plan.yaml with the given steps in the given phase."""
    plan = {
        "feature": "test-feature",
        "schema": "feature",
        "resolved_flags": {},
        "phases": [
            {
                "name": phase,
                "goal": "Test phase goal.",
                "steps": [
                    {
                        "id": sid,
                        "agent": "developer",
                        "goal": "Test phase goal.",
                        "inputs": [],
                        "outputs": ["result"],
                        "rules": ["Keep it scoped."],
                    }
                    for sid in step_ids
                ],
            }
        ],
    }
    (state_dir / "plan.yaml").write_text(yaml.safe_dump(plan, sort_keys=False))


def _make_state_yaml(state_dir: Path, phase: str, steps: list[str]) -> str:
    state = {
        "change_id": "test-feature",
        "slug": "test-feature",
        "schema": "feature",
        "status": "active",
        "repo_root": str(state_dir),
        "flags": {},
        "workflow_plan": {phase: {"active": steps, "filtered": []}},
        "phase": phase,
        "step_history": [],
    }
    path = state_dir / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(path)


def _make_state_obj(phase: str, steps: list[str]):
    """Build an in-memory State object for the given phase/steps."""
    from orchestrator_next.parser import State
    return State(
        change_id="test-feature",
        phase=phase,
        repo_root="/repo",
        workflow_dir="/workflow",
        workflow_plan={phase: {"active": steps, "filtered": []}},
        step_history=[],
        raw={"change_id": "test-feature"},
    )


def _make_state_with_all_completed(phase: str, steps: list[str]):
    """Build a State where all steps in the phase are completed (triggers verify/complete_workflow)."""
    from orchestrator_next.parser import State, StepHistoryEntry

    history = []
    for sid in steps:
        entry = StepHistoryEntry(
            step_id=sid,
            phase=phase,
            status="completed",
            agent="developer",
            attempt=1,
            started_at="2026-01-01T00:00:00Z",
            ended_at="2026-01-01T01:00:00Z",
            usage={},
            escalation=None,
            raw={
                "step_id": sid, "phase": phase, "status": "completed",
                "agent": "developer", "attempt": 1,
            },
        )
        history.append(entry)

    return State(
        change_id="test-feature",
        phase=phase,
        repo_root="/repo",
        workflow_dir="/workflow",
        workflow_plan={phase: {"active": steps, "filtered": []}},
        step_history=history,
        raw={"change_id": "test-feature"},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_run_inline_has_step_context(tmp_path, monkeypatch):
    """run_inline action must carry step_context from plan.yaml."""
    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir()
    _write_contract(contracts_dir, "my-inline-step", {
        "id": "my-inline-step",
        "agent": "inline",
        "instruction": "do inline thing",
        "inputs": [],
        "outputs": [],
        "rules": [],
    })
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts_dir))

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    _write_plan_yaml(state_dir, "implement", ["my-inline-step"])
    state_yaml_path = _make_state_yaml(state_dir, "implement", ["my-inline-step"])

    from orchestrator_next.dispatch import dispatch
    state = _make_state_obj("implement", ["my-inline-step"])
    action, code = dispatch(state, state_yaml_path)

    assert "step_context" in action, "agent/run action must include step_context (ORC-45)"
    ctx = action["step_context"]
    assert ctx["id"] == "my-inline-step"
    assert "rules" in ctx
    assert "goal" in ctx


def test_run_step_has_step_context(tmp_path, monkeypatch):
    """run_step action must carry step_context from plan.yaml."""
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
    _write_plan_yaml(state_dir, "implement", ["my-run-step"])
    state_yaml_path = _make_state_yaml(state_dir, "implement", ["my-run-step"])

    from orchestrator_next.dispatch import dispatch
    state = _make_state_obj("implement", ["my-run-step"])
    action, code = dispatch(state, state_yaml_path)

    assert "step_context" in action, "agent/run action must include step_context (ORC-45)"
    ctx = action["step_context"]
    assert ctx["id"] == "my-run-step"
    assert ctx["rules"] == ["Keep it scoped."]


def test_verify_phase_omits_step_context(tmp_path, monkeypatch):
    """verify_phase action must NOT carry step_context."""
    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts_dir))

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    # plan.yaml present but verify_phase returns before reading it
    _write_plan_yaml(state_dir, "specify", ["done-step"])
    state_yaml_path = _make_state_yaml(state_dir, "specify", ["done-step"])

    from orchestrator_next.dispatch import dispatch

    # Build state with done-step completed AND a verify block in workflow_plan
    from orchestrator_next.parser import State, StepHistoryEntry

    entry = StepHistoryEntry(
        step_id="done-step",
        phase="specify",
        status="completed",
        agent="developer",
        attempt=1,
        started_at="2026-01-01T00:00:00Z",
        ended_at="2026-01-01T01:00:00Z",
        usage={},
        escalation=None,
        raw={"step_id": "done-step", "phase": "specify", "status": "completed",
             "agent": "developer", "attempt": 1},
    )
    state = State(
        change_id="test-feature",
        phase="specify",
        repo_root="/repo",
        workflow_dir="/workflow",
        workflow_plan={
            "specify": {
                "active": ["done-step"],
                "filtered": [],
                "verify": {"assertions": ["design.md exists"]},
            }
        },
        step_history=[entry],
        raw={"change_id": "test-feature"},
    )

    # ORC-45: verify_phase removed; all steps complete → exit 1, empty action dict
    action, code = dispatch(state, state_yaml_path)
    assert code == 1, f"Expected exit 1 (complete_workflow), got {code}"
    assert "step_context" not in action, "complete_workflow (was verify_phase) must NOT include step_context"


def test_missing_plan_yaml_exits_3(tmp_path, monkeypatch):
    """dispatch() must exit with code 3 when plan.yaml is missing."""
    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir()
    _write_contract(contracts_dir, "some-step", {
        "id": "some-step",
        "agent": "inline",
        "instruction": "do thing",
        "inputs": [],
        "outputs": [],
        "rules": [],
    })
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts_dir))

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    # Do NOT write plan.yaml
    state_yaml_path = _make_state_yaml(state_dir, "implement", ["some-step"])

    from orchestrator_next.dispatch import dispatch
    state = _make_state_obj("implement", ["some-step"])

    with pytest.raises(SystemExit) as exc_info:
        dispatch(state, state_yaml_path)
    assert exc_info.value.code == 3


def test_step_missing_in_plan_exits_3(tmp_path, monkeypatch):
    """dispatch() must exit with code 3 when the step_id is not found in plan.yaml."""
    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir()
    _write_contract(contracts_dir, "unlisted-step", {
        "id": "unlisted-step",
        "agent": "inline",
        "instruction": "do thing",
        "inputs": [],
        "outputs": [],
        "rules": [],
    })
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts_dir))

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    # plan.yaml exists but does NOT contain "unlisted-step"
    _write_plan_yaml(state_dir, "implement", ["other-step"])
    state_yaml_path = _make_state_yaml(state_dir, "implement", ["unlisted-step"])

    from orchestrator_next.dispatch import dispatch
    state = _make_state_obj("implement", ["unlisted-step"])

    with pytest.raises(SystemExit) as exc_info:
        dispatch(state, state_yaml_path)
    assert exc_info.value.code == 3
