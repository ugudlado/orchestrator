"""ORC-116: briefing and reason persist into step_history via optional keys."""
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
    """Write a minimal inline step contract (no agent required)."""
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


def test_outputs_briefing_persists_to_step_history(tmp_path, contracts_dir):
    """done payload with outputs.briefing='did X' persists into step_history[-1]."""
    _write_contract(contracts_dir, "explore")
    state_path = _write_state(tmp_path)

    payload = {
        "step_id": "explore",
        "phase": "main",
        "status": "completed",
        "outputs": {"briefing": "did X"},
    }
    result, exit_code = record(state_path, payload)
    assert exit_code == 0, f"record failed: {result}"

    state_after = yaml.safe_load(open(state_path))
    last = state_after["step_history"][-1]
    assert last.get("briefing") == "did X", (
        f"Expected step_history[-1].briefing='did X', got: {last.get('briefing')!r}"
    )


def test_outputs_reason_persists_to_step_history(tmp_path, contracts_dir):
    """done payload with outputs.reason='blocked by Y' persists into step_history[-1]."""
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
    assert last.get("reason") == "blocked by Y", (
        f"Expected step_history[-1].reason='blocked by Y', got: {last.get('reason')!r}"
    )


def test_neither_field_yields_no_briefing_or_reason_keys(tmp_path, contracts_dir):
    """done payload with neither field yields no briefing/reason keys — no error."""
    _write_contract(contracts_dir, "explore")
    state_path = _write_state(tmp_path)

    payload = {
        "step_id": "explore",
        "phase": "main",
        "status": "completed",
        "outputs": {},
    }
    result, exit_code = record(state_path, payload)
    assert exit_code == 0, f"record failed: {result}"

    state_after = yaml.safe_load(open(state_path))
    last = state_after["step_history"][-1]
    assert last.get("briefing") is None
    assert last.get("reason") is None
