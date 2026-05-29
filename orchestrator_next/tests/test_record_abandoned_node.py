"""ORC-75 T-1: Regression test for abandoned re-dispatch loop.

Bug: when record() is called with status: "abandoned" for a node currently
`in_progress`, the node is never flipped to `completed`.  readiness.is_node_ready
only excludes `completed` nodes, so the node re-qualifies and `orchestrator next`
re-dispatches it infinitely.

Expected behaviour after fix (T-2):
  - abandoned record → node status in workflow_plan becomes `completed`
  - abandoned record → state.status becomes `blocked`
  - after abandoned record, is_node_ready returns False for that node
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
from orchestrator_next import readiness  # noqa: E402
from orchestrator_next.parser import load_state  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _state_with_in_progress_node(tmp_path) -> str:
    """Write a state.yaml with one node in_progress and a matching step_history entry."""
    state = {
        "change_id": "orc-75-test",
        "phase": "implement",
        "schema": "feature",
        "workflow_plan": {
            "implement": {
                "nodes": [
                    {
                        "id": "execute-next-task",
                        "status": "in_progress",
                        "agent": "developer",
                        "goal": "Execute tasks",
                        "inputs": [],
                        "outputs": ["task_execution_result"],
                        "rules": [],
                    }
                ],
                "filtered": [],
            }
        },
        "step_history": [
            {
                "step_id": "execute-next-task",
                "phase": "implement",
                "status": "in_progress",
                "agent": "developer",
                "attempt": 1,
                "started_at": "2026-05-22T10:00:00Z",
                "ended_at": None,
            }
        ],
    }
    path = tmp_path / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(path)


# ---------------------------------------------------------------------------
# Tests (must FAIL before T-2 fix)
# ---------------------------------------------------------------------------

class TestAbandonedNodeFlip:
    """Verify abandoned record terminates the DAG node."""

    @pytest.fixture(autouse=True)
    def isolate_contracts(self, tmp_path, monkeypatch):
        """Isolate from real contract files so only node-flip logic runs."""
        empty = tmp_path / "empty_contracts"
        empty.mkdir()
        monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(empty))

    def test_abandoned_flips_node_to_completed(self, tmp_path):
        """abandoned record → node status in workflow_plan must become `completed`."""
        state_path = _state_with_in_progress_node(tmp_path)
        payload = {
            "step_id": "execute-next-task",
            "phase": "implement",
            "status": "abandoned",
            "agent": "developer",
            "outputs": {},
        }
        result, exit_code = record(state_path, payload)
        assert exit_code == 0, f"record() failed unexpectedly: exit_code={exit_code}, result={result}"

        with open(state_path) as f:
            state_raw = yaml.safe_load(f)
        nodes = state_raw["workflow_plan"]["implement"]["nodes"]
        node = next(n for n in nodes if n["id"] == "execute-next-task")
        assert node["status"] == "completed", (
            f"Expected node status 'completed' after abandoned record, got '{node['status']}'. "
            "Bug: abandoned does not flip the node to completed, causing infinite re-dispatch."
        )

    def test_abandoned_sets_state_status_blocked(self, tmp_path):
        """abandoned record → state.status must become `blocked`."""
        state_path = _state_with_in_progress_node(tmp_path)
        payload = {
            "step_id": "execute-next-task",
            "phase": "implement",
            "status": "abandoned",
            "agent": "developer",
            "outputs": {},
        }
        record(state_path, payload)

        with open(state_path) as f:
            state_raw = yaml.safe_load(f)
        assert state_raw.get("status") == "blocked", (
            f"Expected state.status 'blocked', got '{state_raw.get('status')}'"
        )

    def test_abandoned_node_is_not_ready_for_redispatch(self, tmp_path):
        """After abandoned record, is_node_ready must return False (no re-dispatch)."""
        state_path = _state_with_in_progress_node(tmp_path)
        payload = {
            "step_id": "execute-next-task",
            "phase": "implement",
            "status": "abandoned",
            "agent": "developer",
            "outputs": {},
        }
        record(state_path, payload)

        state = load_state(state_path)
        ready = readiness.is_node_ready(state, "execute-next-task")
        assert not ready, (
            "is_node_ready returned True after abandoned record — this causes the "
            "infinite re-dispatch loop (ORC-75 bug)."
        )
