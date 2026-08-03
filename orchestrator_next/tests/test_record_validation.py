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

    def test_accepts_evidence_as_command_list(self, tmp_path):
        """YAML list evidence (common in agent COMPLETION) must not crash record."""
        state_path = _minimal_state(tmp_path)
        payload = {
            "step_id": "design",
            "phase": "specify",
            "status": "completed",
            "agent": "architect",
            "outputs": {"design.md": "spec/changes/test/design.md"},
            "usage": {"input_tokens": 10, "output_tokens": 20},
            "evidence": [{"cmd": "pytest -q", "exit_code": 0}],
        }
        result, exit_code = record(state_path, payload)
        assert exit_code == 0, f"Expected exit_code 0, got {exit_code}: {result}"
        state = yaml.safe_load(Path(state_path).read_text())
        entry = state["step_history"][-1]
        assert entry["evidence"]["commands"] == [{"cmd": "pytest -q", "exit_code": 0}]
        assert entry["evidence"]["outputs"] == {"design.md": "spec/changes/test/design.md"}

    def test_accepts_inline_step_without_usage(self, tmp_path):
        """Script step (no agent in payload) with empty usage → records cleanly."""
        state_path = _minimal_state(tmp_path)
        payload = {
            "step_id": "explore",
            "phase": "specify",
            "status": "completed",
            "outputs": {"discovery_result": {"findings": []}},
            "usage": {},
        }
        result, exit_code = record(state_path, payload)
        assert exit_code == 0, f"Expected exit_code 0, got {exit_code}: {result}"
        assert "step_id" in result  # recorded response contains step_id

    def test_accepts_default_agent_omitted_with_tokens(self, tmp_path):
        """When 'agent' is omitted — no Check B rejection even with empty usage."""
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
        # ORC-117: review_score is now persisted via entry["outputs"], not top-level.
        # Callers must place it in outputs; run_loop hoists root-level keys generically.
        state_path = _minimal_state(tmp_path)
        payload = {
            "step_id": "explore",
            "phase": "specify",
            "status": "completed",
            "outputs": {"review_score": {"overall": 9, "dimensions": {"correctness": 10}}},
        }
        result, exit_code = record(state_path, payload)
        assert exit_code == 0, result
        state = yaml.safe_load(open(state_path))
        assert state["step_history"][-1]["outputs"]["review_score"]["overall"] == 9

    def test_state_patch_retries_absolute_per_key(self, tmp_path):
        state_path = _minimal_state(tmp_path)
        payload = {
            "step_id": "explore",
            "phase": "specify",
            "status": "completed",
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
            "outputs": {},
            "state_patch": {"unexpected_key": True},
        }
        _, exit_code = record(state_path, payload)
        assert exit_code == 0
        assert "state_patch key 'unexpected_key' ignored" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Check D: review verdict enum validation
# ---------------------------------------------------------------------------

def _phase_review_state(tmp_path) -> str:
    state = {
        "change_id": "test-feature",
        "phase": "implement",
        "workflow_plan": {
            "implement": {
                "nodes": [
                    {"id": "review", "status": "in_progress",
                     "agent": "reviewer", "goal": "", "inputs": [],
                     "outputs": ["phase_review_report"], "rules": []},
                ],
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
    step_dir = contracts_dir / "review"
    step_dir.mkdir(parents=True, exist_ok=True)
    contract = {
        "id": "review",
        "agent": "reviewer",
        "inputs": ["task_execution_result"],
        "outputs": ["phase_review_report"],
    }
    (step_dir / "contract.yaml").write_text(yaml.safe_dump(contract, sort_keys=False))
    (step_dir / "prompt.md").write_text("Run phase review.")


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
            "step_id": "review",
            "phase": "implement",
            "status": "completed",
            "agent": "reviewer",
            "outputs": {"phase_review_report": {"verdict": verdict}},
            "usage": {"input_tokens": 1000},
            **extra,
        }

    def test_invalid_verdict_records_without_error(self, tmp_path):
        # ORC-117: engine no longer raises on invalid verdicts; validation is
        # contract-driven via required_outputs_for_completed (added in T-5).
        # A contract without required_outputs_for_completed records as-is.
        state_path = _phase_review_state(tmp_path)
        result, exit_code = record(state_path, self._payload("passed"))
        assert exit_code == 0, result

    def test_missing_verdict_records_without_error(self, tmp_path):
        # ORC-117: engine no longer rejects missing verdict; contract-driven check
        # added in T-5 will coerce status instead.
        state_path = _phase_review_state(tmp_path)
        payload = self._payload("pass")
        payload["outputs"] = {"phase_review_report": {"summary": "no verdict here"}}
        result, exit_code = record(state_path, payload)
        assert exit_code == 0, result

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
        # review_score is no longer copied to top-level entry (ORC-117)
        assert "review_score" not in state["step_history"][-1]

    def test_records_with_unexpected_verdict(self, tmp_path):
        # ORC-117: unexpected verdict is recorded without error; contract-driven
        # enforcement (T-5) will coerce status when required_outputs_for_completed
        # is present in the contract.
        state_path = _phase_review_state(tmp_path)
        result, exit_code = record(state_path, self._payload("PASS"))
        assert exit_code == 0, result


# ===========================================================================
# ORC-63 T-15: upgraded output post-check (AC-10) + node.status / next_step
# ===========================================================================


def _nodes_state(tmp_path, contracts_dir, nodes, phase="main"):
    """Write a state.yaml in the ORC-63 nodes shape; return its path."""
    state = {
        "change_id": "orc63-rec",
        "phase": phase,
        "repo_root": str(tmp_path),
        "workflow_plan": {phase: {"nodes": nodes, "filtered": []}},
        "step_history": [],
    }
    p = tmp_path / "state.yaml"
    p.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(p)


def _write_outputs_contract(contracts_dir, step_id, outputs):
    step_dir = Path(contracts_dir) / step_id
    step_dir.mkdir(parents=True, exist_ok=True)
    (step_dir / "contract.yaml").write_text(yaml.safe_dump({
        "id": step_id, "instruction": "x",
    }))
    (step_dir / "prompt.md").write_text("x")


class TestNodeStatusAndNextStep:
    """AC-3 / OQ-4: a completed record flips the node's status to completed and
    rewrites state.next_step from next_ready_node."""

    @pytest.fixture()
    def contracts_dir(self, tmp_path, monkeypatch):
        d = tmp_path / "steps"
        d.mkdir()
        monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(d))
        return d

    def test_completed_record_flips_node_status(self, tmp_path, contracts_dir):
        """A completed record sets the node's status to completed in state.yaml."""
        _write_outputs_contract(contracts_dir, "a", [])
        _write_outputs_contract(contracts_dir, "b", [])
        sp = _nodes_state(tmp_path, contracts_dir, [
            {"id": "a", "status": "pending", "outputs": []},
            {"id": "b", "status": "pending", "outputs": []},
        ])
        record(sp, {"step_id": "a", "phase": "main", "status": "completed",
                    "agent": "standard",
                    "outputs": {}, "usage": {"input_tokens": 1, "output_tokens": 1}})
        state = yaml.safe_load(open(sp))
        by_id = {n["id"]: n for n in state["workflow_plan"]["main"]["nodes"]}
        assert by_id["a"]["status"] == "completed"

    def test_completed_record_rewrites_next_step(self, tmp_path, contracts_dir):
        """A completed record rewrites state.next_step to the next ready node."""
        _write_outputs_contract(contracts_dir, "a", [])
        _write_outputs_contract(contracts_dir, "b", [])
        sp = _nodes_state(tmp_path, contracts_dir, [
            {"id": "a", "status": "pending", "outputs": []},
            {"id": "b", "status": "pending", "outputs": []},
        ])
        record(sp, {"step_id": "a", "phase": "main", "status": "completed",
                    "agent": "standard",
                    "outputs": {}, "usage": {"input_tokens": 1, "output_tokens": 1}})
        state = yaml.safe_load(open(sp))
        assert state["next_step"] == {"phase": "main", "step_id": "b"}
