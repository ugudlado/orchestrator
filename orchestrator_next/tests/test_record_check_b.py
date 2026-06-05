"""
T-4 / T-5: Tests for record.py Check B (ORC-45).

Check B rule: completed non-inline agent steps must have input_tokens > 0 OR
output_tokens > 0 in the done payload usage block.

Explicit agent_id alone does not bypass zero-token rejection (ORC-45).
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

def _minimal_state(tmp_path) -> str:
    """Write a minimal valid state.yaml to tmp_path and return its path."""
    state = {
        "change_id": "test-feature",
        "phase": "specify",
        "workflow_plan": {
            "specify": {
                "active": ["explore"],
                "filtered": [],
            }
        },
        "step_history": [],
    }
    path = tmp_path / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(path)


class TestCheckBTightening:
    """Check B: agent_id no longer bypasses zero-token rejection."""

    @pytest.fixture(autouse=True)
    def isolate_contracts(self, tmp_path, monkeypatch):
        """Point contract search at empty dir — no contract validation runs."""
        empty = tmp_path / "empty_contracts"
        empty.mkdir()
        monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(empty))

    def test_zero_tokens_with_agent_id_rejected(self, tmp_path):
        """
        RED (T-4): agent step with input_tokens=0, output_tokens=0, agent_id="x"
        must be rejected with agent_step_missing_usage.

        Under the OLD code, agent_id bypasses the check. After T-5, this PASSES.
        """
        state_path = _minimal_state(tmp_path)
        payload = {
            "step_id": "explore",
            "phase": "specify",
            "status": "completed",
            "agent": "discoverer",
            "outputs": {},
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "agent_id": "sess-abc123",  # agent_id present but tokens are zero
        }
        result, exit_code = record(state_path, payload)
        assert exit_code == 3, (
            f"Expected exit_code 3 (agent_step_missing_usage), got {exit_code}: {result}"
        )
        assert result.get("reason") == "agent_step_missing_usage", (
            f"Expected reason=agent_step_missing_usage, got: {result}"
        )

    def test_positive_tokens_with_agent_id_accepted(self, tmp_path):
        """Positive case: agent_id + non-zero tokens → still accepted."""
        state_path = _minimal_state(tmp_path)
        payload = {
            "step_id": "explore",
            "phase": "specify",
            "status": "completed",
            "agent": "discoverer",
            "outputs": {},
            "usage": {"input_tokens": 1000, "output_tokens": 500},
            "agent_id": "sess-abc123",
        }
        result, exit_code = record(state_path, payload)
        assert exit_code == 0, (
            f"Expected exit_code 0, got {exit_code}: {result}"
        )
        assert "step_id" in result, f"Expected recorded response, got: {result}"

    def test_agent_id_in_usage_also_rejected_when_zero_tokens(self, tmp_path):
        """agent_id inside usage dict + zero tokens → also rejected."""
        state_path = _minimal_state(tmp_path)
        payload = {
            "step_id": "explore",
            "phase": "specify",
            "status": "completed",
            "agent": "discoverer",
            "outputs": {},
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "agent_id": "sess-inside-usage",  # agent_id in usage dict
            },
        }
        result, exit_code = record(state_path, payload)
        assert exit_code == 3, (
            f"Expected exit_code 3 (agent_step_missing_usage), got {exit_code}: {result}"
        )
        assert result.get("reason") == "agent_step_missing_usage"

    def test_inline_step_still_accepted_with_zero_tokens(self, tmp_path):
        """Script steps (no agent in payload) are exempt from Check B — still accepted."""
        state_path = _minimal_state(tmp_path)
        payload = {
            "step_id": "explore",
            "phase": "specify",
            "status": "completed",
            "outputs": {},
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
        result, exit_code = record(state_path, payload)
        assert exit_code == 0, (
            f"Expected exit_code 0 for inline step, got {exit_code}: {result}"
        )
