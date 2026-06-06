"""
ORC-48 regression tests: agent + agent_id fields in done payload.

T-1 test cases — must fail before T-2/T-3 land:

1. test_missing_agent_rejected_for_agent_step
   A payload that omits 'agent' for a step whose contract declares
   agent: discoverer must be rejected with reason=payload_missing_agent_for_agent_step.
   (fails at HEAD: record.py defaults agent incorrectly and does not reject)

2. test_agent_recorded_from_payload
   A payload with agent: 'developer' must persist that value in state.yaml.
   (passes at HEAD: record.py uses payload.get("agent"))

3. test_inline_step_no_agent_required
   An inline-script step (no agent: field in contract) with no 'agent' in
   payload succeeds, and state.yaml records agent=None.
   (passes at HEAD)

Red-light verification command (before T-2/T-3):
    pytest config/scripts/orchestrator_next/tests/test_record_agent_field.py -v

Expected before T-2: test 1 FAILS; tests 2, 3 PASS.
Expected after T-2/T-3: all 3 PASS.
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _write_contract(contracts_dir, step_id: str, *, agent: str | None = None, inline: bool = False) -> None:
    """Write a minimal step contract (directory form)."""
    data: dict = {
        "id": step_id,
        "inputs": [],
        "outputs": [],
        "instruction": "test instruction",
    }
    if agent:
        data["agent"] = agent
    if inline:
        data["inline"] = True
    step_dir = contracts_dir / step_id
    step_dir.mkdir(parents=True, exist_ok=True)
    (step_dir / "contract.yaml").write_text(yaml.safe_dump(data))
    if agent and not inline:
        (step_dir / "prompt.md").write_text("test instruction")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRecordAgentField:
    """ORC-48 regression cases for agent/agent_id in done payload."""

    @pytest.fixture()
    def contracts_dir(self, tmp_path, monkeypatch):
        """Isolated contracts directory wired as the contract override."""
        d = tmp_path / "contracts"
        d.mkdir()
        monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(d))
        return d

    # ------------------------------------------------------------------
    # Test 1 (must FAIL at HEAD, PASS after T-2)
    # ------------------------------------------------------------------

    def test_missing_agent_rejected_for_agent_step(self, tmp_path, contracts_dir):
        """
        RED: payload omits 'agent' for a step whose contract declares agent: discoverer.
        Expected: record returns (error_dict, 3) with reason=payload_missing_agent_for_agent_step.
        At HEAD this FAILS because record.py silently defaults agent incorrectly.
        """
        _write_contract(contracts_dir, "diagnose", agent="discoverer")
        state_path = _write_state(tmp_path)

        payload = {
            "step_id": "diagnose",
            "phase": "main",
            "status": "completed",
            "outputs": {"discovery_result": "discovery.md"},
            "usage": {"input_tokens": 74514, "output_tokens": 3210},
            # 'agent' key ABSENT — bug: driver follows SKILL.md template which omits it
        }
        result, exit_code = record(state_path, payload)

        assert exit_code == 3, (
            f"Expected exit_code=3, got {exit_code}. "
            f"record.py is silently defaulting agent incorrectly instead of rejecting the payload. "
            f"Result: {result}"
        )
        assert isinstance(result, dict), f"Expected dict result, got: {result!r}"
        assert result.get("reason") == "payload_missing_agent_for_agent_step", (
            f"Expected reason=payload_missing_agent_for_agent_step, got: {result}"
        )
        assert result.get("expected_agent") == "discoverer", (
            f"Expected expected_agent=discoverer, got: {result}"
        )

    # ------------------------------------------------------------------
    # Test 2 (PASSES at HEAD)
    # ------------------------------------------------------------------

    def test_agent_recorded_from_payload(self, tmp_path, contracts_dir):
        """
        GREEN: payload includes agent='developer'; state.yaml must record that value.
        This is a sanity test that the happy path works (and that T-2 doesn't break it).
        """
        _write_contract(contracts_dir, "diagnose", agent="developer")
        state_path = _write_state(tmp_path)

        payload = {
            "step_id": "diagnose",
            "phase": "main",
            "status": "completed",
            "agent": "developer",
            "outputs": {"discovery_result": "discovery.md"},
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

    # ------------------------------------------------------------------
    # Test 3 (inline step — no agent required)
    # ------------------------------------------------------------------

    def test_inline_step_no_agent_required(self, tmp_path, contracts_dir):
        """
        GREEN: inline-script step (contract has inline: true, no agent:) with no
        'agent' in payload succeeds, and state.yaml records agent=None.
        """
        _write_contract(contracts_dir, "inline-setup", inline=True)
        # Inline steps typically have workflow_plan in a different phase;
        # use a step ID that matches the active list.
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
            "outputs": {},
            "usage": {},
            # 'agent' ABSENT — inline step should not require it
        }
        result, exit_code = record(state_path, payload)

        assert exit_code == 0, (
            f"Expected exit_code=0 for inline step, got {exit_code}: {result}"
        )

        with open(state_path) as f:
            state_after = yaml.safe_load(f)

        last = state_after["step_history"][-1]
        assert last.get("agent") is None, (
            f"Expected step_history[-1].agent=None, got: {last.get('agent')!r}"
        )
