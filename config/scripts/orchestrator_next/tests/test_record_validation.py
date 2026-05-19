"""T-15: RED tests — record.py root-cause validation layer (Checks A, B, C).

All three tests should FAIL before T-16 adds the validation logic.
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
                "active": ["workflow-init"],
                "filtered": [],
            }
        },
        "step_history": [],
    }
    path = tmp_path / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(path)


# ---------------------------------------------------------------------------
# Check A: workflow-init completion with empty/missing active list
# ---------------------------------------------------------------------------

class TestCheckA:

    @pytest.fixture(autouse=True)
    def isolate_contracts(self, tmp_path, monkeypatch):
        """Isolate from real contract files so only Check A logic runs."""
        empty = tmp_path / "empty_contracts"
        empty.mkdir()
        monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(empty))

    def test_rejects_workflow_init_empty_active(self, tmp_path):
        """workflow_plan.active is empty list → exit 3, validation_error."""
        state_path = _minimal_state(tmp_path)
        payload = {
            "step_id": "workflow-init",
            "phase": "specify",
            "status": "completed",
            "agent": "workflow-init",
            "outputs": {
                "slug": "x",
                "worktree_path": None,
                "branch": None,
                "ticket_id": None,
                "workflow_plan": {
                    "specify": {"active": [], "filtered": []},
                },
                "resolved_flags": {},
            },
            "usage": {"input_tokens": 100},
        }
        result, exit_code = record(state_path, payload)
        assert exit_code == 3, f"Expected exit_code 3, got {exit_code}: {result}"
        assert result["reason"] == "workflow_plan_active_missing_or_empty"
        assert "specify" in result["phases"]

    def test_rejects_workflow_init_missing_active_key(self, tmp_path):
        """workflow_plan phase body is missing 'active' key → exit 3."""
        state_path = _minimal_state(tmp_path)
        payload = {
            "step_id": "workflow-init",
            "phase": "specify",
            "status": "completed",
            "agent": "workflow-init",
            "outputs": {
                "slug": "x",
                "worktree_path": None,
                "branch": None,
                "ticket_id": None,
                "workflow_plan": {
                    "specify": {"active": ["workflow-init"], "filtered": []},
                    "implement": {"filtered": []},  # missing 'active' key
                },
                "resolved_flags": {},
            },
            "usage": {"input_tokens": 100},
        }
        result, exit_code = record(state_path, payload)
        assert exit_code == 3, f"Expected exit_code 3, got {exit_code}: {result}"
        assert result["reason"] == "workflow_plan_active_missing_or_empty"
        assert "implement" in result["phases"]

    def test_state_yaml_unchanged_on_check_a_rejection(self, tmp_path):
        """On Check A rejection, state.yaml must be byte-equal to its pre-call content."""
        state_path = _minimal_state(tmp_path)
        pre_bytes = open(state_path, "rb").read()
        payload = {
            "step_id": "workflow-init",
            "phase": "specify",
            "status": "completed",
            "agent": "workflow-init",
            "outputs": {
                "slug": "x",
                "worktree_path": None,
                "branch": None,
                "ticket_id": None,
                "workflow_plan": {
                    "specify": {"active": [], "filtered": []},
                },
                "resolved_flags": {},
            },
            "usage": {"input_tokens": 100},
        }
        record(state_path, payload)
        post_bytes = open(state_path, "rb").read()
        assert pre_bytes == post_bytes, "state.yaml was modified despite Check A rejection"


# ---------------------------------------------------------------------------
# Check B: agent step missing usage
# ---------------------------------------------------------------------------

class TestCheckB:

    @pytest.fixture(autouse=True)
    def isolate_contracts(self, tmp_path, monkeypatch):
        """Isolate from real contract files so only Check B logic runs."""
        empty = tmp_path / "empty_contracts"
        empty.mkdir()
        monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(empty))

    def test_rejects_agent_step_without_usage(self, tmp_path):
        """Agent step with empty usage → exit 3, agent_step_missing_usage."""
        state_path = _minimal_state(tmp_path)
        payload = {
            "step_id": "explore",
            "phase": "specify",
            "status": "completed",
            "agent": "discoverer",
            "outputs": {"discovery_result": {"findings": []}},
            "usage": {},
        }
        result, exit_code = record(state_path, payload)
        assert exit_code == 3, f"Expected exit_code 3, got {exit_code}: {result}"
        assert result["reason"] == "agent_step_missing_usage"

    def test_rejects_agent_step_with_zero_tokens(self, tmp_path):
        """Agent step with usage.input_tokens=0 → exit 3 (zero is not valid)."""
        state_path = _minimal_state(tmp_path)
        payload = {
            "step_id": "explore",
            "phase": "specify",
            "status": "completed",
            "agent": "discoverer",
            "outputs": {"discovery_result": {"findings": []}},
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
        result, exit_code = record(state_path, payload)
        assert exit_code == 3, f"Expected exit_code 3, got {exit_code}: {result}"
        assert result["reason"] == "agent_step_missing_usage"

    def test_accepts_agent_step_with_input_tokens(self, tmp_path):
        """Positive case: agent step with usage.input_tokens=1000 → exit 0, recorded."""
        state_path = _minimal_state(tmp_path)
        payload = {
            "step_id": "explore",
            "phase": "specify",
            "status": "completed",
            "agent": "discoverer",
            "outputs": {"discovery_result": {"findings": []}},
            "usage": {"input_tokens": 1000},
        }
        result, exit_code = record(state_path, payload)
        assert exit_code == 0, f"Expected exit_code 0, got {exit_code}: {result}"
        assert "step_id" in result  # recorded response contains step_id

    def test_accepts_inline_step_without_usage(self, tmp_path):
        """Inline step (agent='inline') with empty usage → records cleanly."""
        state_path = _minimal_state(tmp_path)
        payload = {
            "step_id": "explore",
            "phase": "specify",
            "status": "completed",
            "agent": "inline",
            "outputs": {"discovery_result": {"findings": []}},
            "usage": {},
        }
        result, exit_code = record(state_path, payload)
        assert exit_code == 0, f"Expected exit_code 0, got {exit_code}: {result}"
        assert "step_id" in result  # recorded response contains step_id

    def test_accepts_default_agent_omitted_with_tokens(self, tmp_path):
        """When 'agent' is omitted it defaults to 'inline' — no rejection even with empty usage."""
        state_path = _minimal_state(tmp_path)
        payload = {
            "step_id": "explore",
            "phase": "specify",
            "status": "completed",
            # no 'agent' key
            "outputs": {"discovery_result": {"findings": []}},
            "usage": {},
        }
        result, exit_code = record(state_path, payload)
        assert exit_code == 0, f"Expected exit_code 0 for implicit-inline, got {exit_code}: {result}"
        assert "step_id" in result  # recorded response contains step_id

    def test_state_yaml_unchanged_on_check_b_rejection(self, tmp_path):
        """On Check B rejection, state.yaml must be byte-equal to its pre-call content."""
        state_path = _minimal_state(tmp_path)
        pre_bytes = open(state_path, "rb").read()
        payload = {
            "step_id": "explore",
            "phase": "specify",
            "status": "completed",
            "agent": "discoverer",
            "outputs": {"discovery_result": {"findings": []}},
            "usage": {},
        }
        record(state_path, payload)
        post_bytes = open(state_path, "rb").read()
        assert pre_bytes == post_bytes, "state.yaml was modified despite Check B rejection"


# ---------------------------------------------------------------------------
# Check C: corrupted state.yaml is detected; file is restored
# ---------------------------------------------------------------------------

class TestCheckC:

    @pytest.fixture(autouse=True)
    def isolate_contracts(self, tmp_path, monkeypatch):
        """Point contract search to an empty dir so load_contract_for_step raises
        FileNotFoundError (contract=None) for all step IDs used in Check C tests.
        This prevents the existing missing_outputs check from triggering first."""
        empty_contracts = tmp_path / "empty_contracts"
        empty_contracts.mkdir()
        monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(empty_contracts))

    def test_rejects_corrupted_state_yaml_before_write(self, tmp_path):
        """Pre-corrupt state.yaml → exit 4, error, state_yaml_parse_failure."""
        state_path = _minimal_state(tmp_path)
        # Overwrite with bytes that are invalid YAML.
        corrupted = b"!!invalid: !!!\nkey: [\n"
        with open(state_path, "wb") as f:
            f.write(corrupted)

        payload = {
            "step_id": "explore",
            "phase": "specify",
            "status": "completed",
            "agent": "inline",
            "outputs": {},
            "usage": {},
        }
        result, exit_code = record(state_path, payload)
        assert exit_code == 4, f"Expected exit_code 4, got {exit_code}: {result}"
        assert result["reason"] == "state_yaml_parse_failure"

    def test_restores_state_yaml_on_parse_failure(self, tmp_path):
        """After Check C detection, file must be byte-equal to pre-call state."""
        state_path = _minimal_state(tmp_path)
        corrupted = b"!!invalid: !!!\nkey: [\n"
        with open(state_path, "wb") as f:
            f.write(corrupted)
        pre_bytes = corrupted  # That's what was there before the call

        payload = {
            "step_id": "explore",
            "phase": "specify",
            "status": "completed",
            "agent": "inline",
            "outputs": {},
            "usage": {},
        }
        record(state_path, payload)
        post_bytes = open(state_path, "rb").read()
        assert pre_bytes == post_bytes, "state.yaml was not restored after Check C parse failure"
