"""ORC-117 T-2: generic contract-driven required-output check."""
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


def _write_state(tmp_path) -> str:
    state = {
        "change_id": "orc-117-t2",
        "phase": "main",
        "workflow_plan": {"main": {"active": ["design-review"], "filtered": []}},
        "step_history": [],
    }
    path = tmp_path / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(path)


def _write_contract(contracts_dir, step_id: str, *, required_outputs=None) -> None:
    data = {"id": step_id}
    if required_outputs:
        data["required_outputs_for_completed"] = required_outputs
    step_dir = contracts_dir / step_id
    step_dir.mkdir(parents=True, exist_ok=True)
    (step_dir / "contract.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    # Colocated prompt.md so parser resolves it without a prompt: field
    (step_dir / "prompt.md").write_text("test prompt")


@pytest.fixture()
def contracts_dir(tmp_path, monkeypatch):
    d = tmp_path / "contracts"
    d.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(d))
    return d


def test_completed_with_matching_required_output_passes(tmp_path, contracts_dir):
    """Contract with required_outputs_for_completed=[{key, value}]; matching payload stays completed."""
    _write_contract(contracts_dir, "design-review", required_outputs=[{"key": "design_review_result", "value": "pass"}])
    state_path = _write_state(tmp_path)

    payload = {
        "step_id": "design-review",
        "phase": "main",
        "status": "completed",
        "agent": "standard",
        "outputs": {"reason": "test", "design_review_result": "pass"},
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    result, exit_code = record(state_path, payload)
    assert exit_code == 0, result
    state = yaml.safe_load(open(state_path))
    assert state["step_history"][-1]["status"] == "completed"


def test_completed_with_mismatched_required_output_coerces_to_failed(tmp_path, contracts_dir, capsys):
    """Mismatched required output coerces status to failed with stderr note."""
    _write_contract(contracts_dir, "design-review", required_outputs=[{"key": "design_review_result", "value": "pass"}])
    state_path = _write_state(tmp_path)

    payload = {
        "step_id": "design-review",
        "phase": "main",
        "status": "completed",
        "outputs": {"reason": "test", "design_review_result": "needs_work"},
    }
    result, exit_code = record(state_path, payload)
    assert exit_code == 0, result
    state = yaml.safe_load(open(state_path))
    assert state["step_history"][-1]["status"] == "failed"
    captured = capsys.readouterr()
    import re
    assert re.search(r"coercing status.*failed", captured.err)


def test_dotted_path_lookup(tmp_path, contracts_dir):
    """Contract key with dotted path resolves one level deep."""
    _write_contract(contracts_dir, "review", required_outputs=[{"key": "phase_review_report.verdict", "value": "pass"}])
    state = {
        "change_id": "orc-117-t2",
        "phase": "main",
        "workflow_plan": {"main": {"active": ["review"], "filtered": []}},
        "step_history": [],
    }
    state_path = tmp_path / "state2.yaml"
    state_path.write_text(yaml.safe_dump(state, sort_keys=False))

    payload = {
        "step_id": "review",
        "phase": "main",
        "status": "completed",
        "outputs": {"reason": "test", "phase_review_report": {"verdict": "needs_work"}},
    }
    result, exit_code = record(str(state_path), payload)
    assert exit_code == 0, result
    st = yaml.safe_load(open(state_path))
    assert st["step_history"][-1]["status"] == "failed"


def test_no_contract_no_check(tmp_path, contracts_dir):
    """Step with no contract records normally without crash."""
    # No contract written for "no-contract-step"
    state = {
        "change_id": "orc-117-t2",
        "phase": "main",
        "workflow_plan": {"main": {"active": ["no-contract-step"], "filtered": []}},
        "step_history": [],
    }
    state_path = tmp_path / "state3.yaml"
    state_path.write_text(yaml.safe_dump(state, sort_keys=False))

    payload = {
        "step_id": "no-contract-step",
        "phase": "main",
        "status": "completed",
        "outputs": {"reason": "test", "something": "value"},
    }
    result, exit_code = record(str(state_path), payload)
    assert exit_code == 0, result


def test_deleted_functions_absent():
    """Deleted validator functions are no longer importable from record."""
    from orchestrator_next import record as rec
    assert not hasattr(rec, "_validate_phase_review_output")
    assert not hasattr(rec, "_validate_design_review_output")
    assert not hasattr(rec, "_normalize_review_payload_status")
