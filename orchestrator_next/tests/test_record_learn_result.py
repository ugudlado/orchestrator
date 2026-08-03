"""learn done payload: default_outputs supplement (shell loop)."""
from __future__ import annotations

import os
import sys

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from orchestrator_next.record import record  # noqa: E402


def _state_path(tmp_path) -> str:
    state = {
        "change_id": "orc-learn-test",
        "phase": "main",
        "repo_root": str(tmp_path),
        "workflow_plan": {
            "main": {
                "nodes": [{"id": "learn", "status": "in_progress"}],
                "filtered": [],
            }
        },
        "step_history": [],
    }
    path = tmp_path / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(path)


def test_run_learn_cycle_empty_outputs_ok_without_defaults(tmp_path):
    state_path = _state_path(tmp_path)
    payload = {
        "step_id": "learn",
        "phase": "main",
        "status": "completed",
        "agent": "workflow-learner",
        "outputs": {},
        "usage": {"input_tokens": 10, "output_tokens": 5, "model": "claude-sonnet-4-6"},
    }
    result, code = record(state_path, payload)
    assert code == 0, result
    state = yaml.safe_load(open(state_path))
    last = state["step_history"][-1]
    assert last["step_id"] == "learn"
    outputs = (last.get("evidence") or {}).get("outputs") or {}
    assert "backlog_tickets_synced" not in outputs
    assert "learn_result" not in outputs


def test_run_learn_cycle_accepts_empty_backlog_tickets_synced_list(tmp_path):
    state_path = _state_path(tmp_path)
    payload = {
        "step_id": "learn",
        "phase": "main",
        "status": "completed",
        "agent": "workflow-learner",
        "outputs": {
            "backlog_tickets_synced": [],
        },
        "usage": {"input_tokens": 10, "output_tokens": 5, "model": "claude-sonnet-4-6"},
    }
    result, code = record(state_path, payload)
    assert code == 0, result
