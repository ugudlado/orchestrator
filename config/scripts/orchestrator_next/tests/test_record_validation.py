"""T-15: Regression tests for record.py validation."""
from __future__ import annotations

import os
import sys

import pytest
import yaml
from pathlib import Path

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


class TestOptionalPayloadFields:
    """done-payload optional fields persist via record.py passthrough."""

    @pytest.fixture(autouse=True)
    def isolate_contracts(self, tmp_path, monkeypatch):
        empty = tmp_path / "empty_contracts"
        empty.mkdir()
        monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(empty))

    def test_review_score_passthrough_to_step_history(self, tmp_path):
        state_path = _minimal_state(tmp_path)
        payload = {
            "step_id": "explore",
            "phase": "specify",
            "status": "completed",
            "agent": "inline",
            "outputs": {},
            "review_score": {"overall": 9, "dimensions": {"correctness": 10}},
        }
        result, exit_code = record(state_path, payload)
        assert exit_code == 0, result
        state = yaml.safe_load(open(state_path))
        assert state["step_history"][-1]["review_score"]["overall"] == 9

    def test_state_patch_retries_absolute_per_key(self, tmp_path):
        state_path = _minimal_state(tmp_path)
        payload = {
            "step_id": "explore",
            "phase": "specify",
            "status": "completed",
            "agent": "inline",
            "outputs": {},
            "state_patch": {"retries": {"execute-next-task": 2}},
        }
        result, exit_code = record(state_path, payload)
        assert exit_code == 0, result
        state = yaml.safe_load(open(state_path))
        assert state["retries"]["execute-next-task"] == 2

        payload2 = {
            "step_id": "explore",
            "phase": "specify",
            "status": "completed",
            "agent": "inline",
            "outputs": {},
            "state_patch": {"retries": {"execute-next-task": 3, "T-1": 1}},
        }
        result, exit_code = record(state_path, payload2)
        assert exit_code == 0, result
        state = yaml.safe_load(open(state_path))
        assert state["retries"] == {"execute-next-task": 3, "T-1": 1}

    def test_state_patch_unknown_key_emits_warning(self, tmp_path, capsys):
        state_path = _minimal_state(tmp_path)
        payload = {
            "step_id": "explore",
            "phase": "specify",
            "status": "completed",
            "agent": "inline",
            "outputs": {},
            "state_patch": {"unexpected_key": True},
        }
        _, exit_code = record(state_path, payload)
        assert exit_code == 0
        assert "state_patch key 'unexpected_key' ignored" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Check D: run-phase-review verdict enum validation
# ---------------------------------------------------------------------------

def _phase_review_state(tmp_path) -> str:
    state = {
        "change_id": "test-feature",
        "phase": "implement",
        "workflow_plan": {
            "implement": {
                "active": ["run-phase-review"],
                "filtered": [],
            }
        },
        "step_history": [],
    }
    path = tmp_path / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(path)


def _write_run_phase_review_contract(contracts_dir: Path) -> None:
    contracts_dir.mkdir(parents=True, exist_ok=True)
    contract = {
        "id": "run-phase-review",
        "agent": "reviewer",
        "inputs": ["task_execution_result"],
        "outputs": ["phase_review_report"],
    }
    (contracts_dir / "run-phase-review.yaml").write_text(
        yaml.safe_dump(contract, sort_keys=False)
    )


class TestPhaseReviewVerdictValidation:

    @pytest.fixture(autouse=True)
    def contracts(self, tmp_path, monkeypatch):
        contracts_dir = tmp_path / "contracts"
        _write_run_phase_review_contract(contracts_dir)
        monkeypatch.setenv(
            "ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts_dir)
        )

    def _payload(self, verdict: str, **extra) -> dict:
        return {
            "step_id": "run-phase-review",
            "phase": "implement",
            "status": "completed",
            "agent": "reviewer",
            "outputs": {"phase_review_report": {"verdict": verdict}},
            "usage": {"input_tokens": 1000},
            **extra,
        }

    def test_rejects_invalid_verdict(self, tmp_path):
        state_path = _phase_review_state(tmp_path)
        result, exit_code = record(state_path, self._payload("passed"))
        assert exit_code == 3
        assert result["reason"] == "invalid_phase_review_verdict"
        assert result["valid_verdicts"] == ["incomplete_phase", "needs_work", "pass"]

    def test_rejects_missing_verdict(self, tmp_path):
        state_path = _phase_review_state(tmp_path)
        payload = self._payload("pass")
        payload["outputs"] = {"phase_review_report": {}}
        result, exit_code = record(state_path, payload)
        assert exit_code == 3
        assert result["reason"] == "invalid_phase_review_verdict"

    def test_accepts_pass_verdict(self, tmp_path):
        state_path = _phase_review_state(tmp_path)
        result, exit_code = record(
            state_path,
            self._payload("pass", review_score={"overall": 9, "dimensions": {}}),
        )
        assert exit_code == 0, result

    def test_accepts_incomplete_phase_without_review_score(self, tmp_path):
        state_path = _phase_review_state(tmp_path)
        result, exit_code = record(state_path, self._payload("incomplete_phase"))
        assert exit_code == 0, result
        state = yaml.safe_load(open(state_path))
        assert "review_score" not in state["step_history"][-1]

    def test_state_yaml_unchanged_on_verdict_rejection(self, tmp_path):
        state_path = _phase_review_state(tmp_path)
        pre_bytes = open(state_path, "rb").read()
        record(state_path, self._payload("PASS"))
        post_bytes = open(state_path, "rb").read()
        assert pre_bytes == post_bytes
