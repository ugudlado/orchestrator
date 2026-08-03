"""
ORC-48 regression tests: agent + agent_id fields in done payload.
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

from orchestrator_next.record import record  # noqa: E402


def _write_state(tmp_path, *, repo_root: str = "/tmp") -> str:
    """Write a minimal valid state.yaml and return its path string."""
    state = {
        "schema": "bugfix",
        "change_id": "orc-48-test",
        "repo_root": repo_root,
        "phase": "main",
        "workflow_plan": {
            "main": {
                "active": ["diagnose"],
                "filtered": [],
            }
        },
        "step_history": [],
    }
    path = tmp_path / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(path)


def _write_contract(contracts_dir, step_id: str, *, agent: bool = True) -> None:
    """Write a minimal step contract (directory form)."""
    step_dir = contracts_dir / step_id
    step_dir.mkdir(parents=True, exist_ok=True)
    if agent:
        data = {"id": step_id, "version": 1, "prompt": "prompt.md"}
        (step_dir / "prompt.md").write_text("test instruction")
    else:
        data = {"id": step_id, "version": 1, "run": "script.sh"}
        (step_dir / "script.sh").write_text("#!/bin/sh\nexit 0\n")
    (step_dir / "contract.yaml").write_text(yaml.safe_dump(data))


class TestRecordAgentField:
    """ORC-48 regression cases for agent/agent_id in done payload."""

    @pytest.fixture()
    def contracts_dir(self, tmp_path, monkeypatch):
        """Isolated contracts directory wired as the contract override."""
        d = tmp_path / "contracts"
        d.mkdir()
        monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(d))
        return d

    def test_missing_agent_rejected_for_agent_step(self, tmp_path, contracts_dir):
        _write_contract(contracts_dir, "diagnose", agent=True)
        state_path = _write_state(tmp_path)

        payload = {
            "step_id": "diagnose",
            "phase": "main",
            "status": "completed",
            "outputs": {"reason": "test", "discovery_result": "discovery.md"},
            "usage": {"input_tokens": 74514, "output_tokens": 3210},
        }
        result, exit_code = record(state_path, payload)

        assert exit_code == 3, (
            f"Expected exit_code=3, got {exit_code}. Result: {result}"
        )
        assert isinstance(result, dict), f"Expected dict result, got: {result!r}"
        assert result.get("reason") == "payload_missing_agent_for_agent_step", (
            f"Expected reason=payload_missing_agent_for_agent_step, got: {result}"
        )

    def test_agent_recorded_from_payload(self, tmp_path, contracts_dir):
        _write_contract(contracts_dir, "diagnose", agent=True)
        state_path = _write_state(tmp_path)

        payload = {
            "step_id": "diagnose",
            "phase": "main",
            "status": "completed",
            "agent": "developer",
            "outputs": {"reason": "test", "discovery_result": "discovery.md"},
            "usage": {"input_tokens": 5000, "output_tokens": 1000},
        }
        result, exit_code = record(state_path, payload)

        assert exit_code == 0, f"Expected exit_code=0, got {exit_code}: {result}"

        with open(state_path) as f:
            state_after = yaml.safe_load(f)

        last = state_after["step_history"][-1]
        assert last["agent"] == "developer", (
            f"Expected step_history[-1].agent='developer', got: {last['agent']!r}"
        )

    def test_inline_step_no_agent_required(self, tmp_path, contracts_dir):
        _write_contract(contracts_dir, "inline-setup", agent=False)
        state = {
            "schema": "bugfix",
            "change_id": "orc-48-test",
            "repo_root": "/tmp",
            "phase": "main",
            "workflow_plan": {
                "main": {
                    "active": ["inline-setup"],
                    "filtered": [],
                }
            },
            "step_history": [],
        }
        state_path = str(tmp_path / "state.yaml")
        with open(state_path, "w") as f:
            yaml.safe_dump(state, f)

        payload = {
            "step_id": "inline-setup",
            "phase": "main",
            "status": "completed",
            "outputs": {"reason": "test"},
            "usage": {},
        }
        result, exit_code = record(state_path, payload)

        assert exit_code == 0, (
            f"Expected exit_code=0 for script step, got {exit_code}: {result}"
        )

        with open(state_path) as f:
            state_after = yaml.safe_load(f)

        last = state_after["step_history"][-1]
        assert last.get("agent") is None, (
            f"Expected step_history[-1].agent=None, got: {last.get('agent')!r}"
        )
