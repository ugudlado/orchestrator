"""
T-2 (ORC-18): RED tests for dispatch file-not-found guards (ContractDispatchError + /doctor hint).

Fails until T-5 wraps contract/agent reads in dispatch.py (and exports ContractDispatchError).
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

from orchestrator_next.parser import StepContract


def _write_state(tmp_path, change_id: str = "guard-test") -> str:
    state_path = tmp_path / "state.yaml"
    state_path.write_text(yaml.dump({
        "change_id": change_id,
        "phase": "implement",
        "repo_root": str(tmp_path),
        "workflow_plan": {"implement": {"active": ["missing-step"], "filtered": []}},
        "step_history": [],
    }))
    return str(state_path)


class TestDispatchContractMissing:

    def test_load_contract_missing_raises_contract_dispatch_error(self, tmp_path, monkeypatch):
        """Missing step contract → ContractDispatchError with path and 'Run /doctor'."""
        steps_dir = tmp_path / "steps"
        steps_dir.mkdir()
        monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(steps_dir))
        state_yaml = _write_state(tmp_path)

        from orchestrator_next.dispatch import ContractDispatchError, _load_step_contract

        with pytest.raises(ContractDispatchError) as exc_info:
            _load_step_contract("missing-step", state_yaml)

        msg = str(exc_info.value)
        assert "Run /doctor" in msg
        assert "missing-step" in msg


class TestDispatchAgentMissing:
    """T-1 confirmed agent .md read via resolver.load_agent_tools on dispatch path."""

    def test_resolve_allowed_tools_missing_agent_raises(self, tmp_path, monkeypatch):
        """Missing agent file with allowed_tools → ContractDispatchError + /doctor hint."""
        orch_home = tmp_path / "orch"
        (orch_home / "agents").mkdir(parents=True)
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(orch_home))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        (tmp_path / "home" / ".claude" / "agents").mkdir(parents=True)

        from orchestrator_next.dispatch import ContractDispatchError, _resolve_allowed_tools

        contract = StepContract(
            id="needs-agent",
            agent="ghost-agent",
            run=None,
            instruction="",
            rules=[],
            allowed_tools=["Read"],
        )

        with pytest.raises(ContractDispatchError) as exc_info:
            _resolve_allowed_tools(contract)

        msg = str(exc_info.value)
        assert "Run /doctor" in msg
        assert "ghost-agent" in msg
