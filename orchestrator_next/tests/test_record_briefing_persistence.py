"""outputs.reason is required on every recorded outcome."""
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
    state = {
        "schema": "feature",
        "change_id": "orc-116-test",
        "repo_root": repo_root,
        "phase": "main",
        "workflow_plan": {
            "main": {
                "active": ["explore"],
                "filtered": [],
            }
        },
        "step_history": [],
    }
    path = tmp_path / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(path)


def _write_contract(contracts_dir, step_id: str) -> None:
    data = {
        "id": step_id,
        "inputs": [],
        "outputs": [],
        "instruction": "test instruction",
        "inline": True,
    }
    step_dir = contracts_dir / step_id
    step_dir.mkdir(parents=True, exist_ok=True)
    (step_dir / "contract.yaml").write_text(yaml.safe_dump(data))


@pytest.fixture()
def contracts_dir(tmp_path, monkeypatch):
    d = tmp_path / "contracts"
    d.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(d))
    return d


def test_outputs_reason_persists_on_completed(tmp_path, contracts_dir):
    _write_contract(contracts_dir, "explore")
    state_path = _write_state(tmp_path)

    payload = {
        "step_id": "explore",
        "phase": "main",
        "status": "completed",
        "outputs": {"reason": "did X"},
    }
    result, exit_code = record(state_path, payload)
    assert exit_code == 0, f"record failed: {result}"

    state_after = yaml.safe_load(open(state_path))
    last = state_after["step_history"][-1]
    assert last.get("outputs", {}).get("reason") == "did X"


def test_outputs_reason_persists_on_abandoned(tmp_path, contracts_dir):
    _write_contract(contracts_dir, "explore")
    state_path = _write_state(tmp_path)

    payload = {
        "step_id": "explore",
        "phase": "main",
        "status": "abandoned",
        "outputs": {"reason": "blocked by Y"},
    }
    result, exit_code = record(state_path, payload)
    assert exit_code == 0, f"record failed: {result}"

    state_after = yaml.safe_load(open(state_path))
    last = state_after["step_history"][-1]
    assert last.get("outputs", {}).get("reason") == "blocked by Y"


@pytest.mark.parametrize("status", ["completed", "failed", "abandoned", "recovered"])
def test_missing_reason_rejected(tmp_path, contracts_dir, status):
    _write_contract(contracts_dir, "explore")
    state_path = _write_state(tmp_path)

    payload = {
        "step_id": "explore",
        "phase": "main",
        "status": status,
        "outputs": {},
    }
    result, exit_code = record(state_path, payload)
    assert exit_code == 3
    assert result.get("reason") == "missing_reason"


def test_blank_reason_rejected(tmp_path, contracts_dir):
    _write_contract(contracts_dir, "explore")
    state_path = _write_state(tmp_path)

    payload = {
        "step_id": "explore",
        "phase": "main",
        "status": "completed",
        "outputs": {"reason": "   "},
    }
    result, exit_code = record(state_path, payload)
    assert exit_code == 3
    assert result.get("reason") == "missing_reason"


def test_briefing_alone_does_not_satisfy_reason(tmp_path, contracts_dir):
    _write_contract(contracts_dir, "explore")
    state_path = _write_state(tmp_path)

    payload = {
        "step_id": "explore",
        "phase": "main",
        "status": "completed",
        "outputs": {"briefing": "legacy text"},
    }
    result, exit_code = record(state_path, payload)
    assert exit_code == 3
    assert result.get("reason") == "missing_reason"
