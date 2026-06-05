"""ORC-81: dispatch resume refuses steps not in workflow_plan."""
from __future__ import annotations

import os
import sys

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from orchestrator_next.dispatch import dispatch  # noqa: E402
from orchestrator_next.parser import State, StepHistoryEntry  # noqa: E402


def _state_with_last(step_id: str) -> State:
    return State(
        change_id="feat",
        phase="implement",
        repo_root="/repo",
        workflow_dir="/repo",
        workflow_plan={
            "implement": {"nodes": [{"id": "preview-route"}]},
        },
        step_history=[
            StepHistoryEntry(
                step_id=step_id,
                phase="implement",
                status="in_progress",
                agent="developer",
                attempt=1,
                started_at="2024-01-01T00:00:00Z",
                ended_at=None,
                usage={},
                escalation=None,
                raw={
                    "step_id": step_id,
                    "phase": "implement",
                    "status": "in_progress",
                    "attempt": 1,
                },
            )
        ],
        raw={},
    )


def test_resume_ghost_step_exits_3(tmp_path, capsys):
    state_path = tmp_path / "state.yaml"
    state_path.write_text(yaml.safe_dump({"change_id": "feat", "phase": "implement"}))
    action, code = dispatch(_state_with_last("ghost-step"), str(state_path))
    assert action == {}
    assert code == 3
    assert "ghost-step" in capsys.readouterr().err


def test_resume_in_plan_step_ok(tmp_path):
    state_path = tmp_path / "state.yaml"
    state_path.write_text(yaml.safe_dump({"change_id": "feat", "phase": "implement"}))
    action, code = dispatch(_state_with_last("preview-route"), str(state_path))
    assert code == 0
    assert action.get("is_resume") is True
    assert action.get("step_id") == "preview-route"
