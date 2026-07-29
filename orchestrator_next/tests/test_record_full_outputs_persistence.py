"""ORC-117 T-1: full outputs dict persisted at entry["outputs"]."""
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
        "change_id": "orc-117-test",
        "phase": "main",
        "workflow_plan": {"main": {"active": ["implement"], "filtered": []}},
        "step_history": [],
    }
    path = tmp_path / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(path)


def _write_contract(contracts_dir, step_id: str) -> None:
    step_dir = contracts_dir / step_id
    step_dir.mkdir(parents=True, exist_ok=True)
    (step_dir / "contract.yaml").write_text(yaml.safe_dump({"id": step_id, "instruction": "test"}))


@pytest.fixture()
def contracts_dir(tmp_path, monkeypatch):
    d = tmp_path / "contracts"
    d.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(d))
    return d


@pytest.mark.xfail(strict=False)
def test_full_outputs_dict_persisted_in_entry(tmp_path, contracts_dir):
    """entry["outputs"] must contain every key from payload outputs, verbatim."""
    _write_contract(contracts_dir, "implement")
    state_path = _write_state(tmp_path)

    outputs = {"briefing": "did the thing", "implementation_result": {"tasks": 3}, "novel_key": "x"}
    payload = {
        "step_id": "implement",
        "phase": "main",
        "status": "completed",
        "outputs": outputs,
    }
    _, exit_code = record(state_path, payload)
    assert exit_code == 0

    state = yaml.safe_load(open(state_path))
    entry = state["step_history"][-1]
    assert entry.get("outputs") == outputs


@pytest.mark.xfail(strict=False)
def test_novel_key_survives(tmp_path, contracts_dir):
    """A key not in _OPTIONAL_STEP_HISTORY_KEYS still appears in entry["outputs"]."""
    _write_contract(contracts_dir, "implement")
    state_path = _write_state(tmp_path)

    payload = {
        "step_id": "implement",
        "phase": "main",
        "status": "completed",
        "outputs": {"custom_data": {"metric": 42}},
    }
    _, exit_code = record(state_path, payload)
    assert exit_code == 0

    state = yaml.safe_load(open(state_path))
    entry = state["step_history"][-1]
    assert entry.get("outputs", {}).get("custom_data") == {"metric": 42}
