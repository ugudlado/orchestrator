"""Statechart routing: on_failure rework loop for run-phase-review.

Tests cover end-to-end record() behavior with on_failure edges declared on
workflow nodes. Review steps emit status: failed + verdict: needs_work —
_resolve_routing picks on_failure → implement-tasks.
"""
from __future__ import annotations

import os
import sys

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from orchestrator_next import record  # noqa: E402


# ---------------------------------------------------------------------------
# _resolve_routing
# ---------------------------------------------------------------------------

class TestResolveRouting:

    def _state_with_edge(self, step_id, on_success=None, on_failure=None, max_retries=None, retries=None):
        node = {"id": step_id, "status": "in_progress"}
        if on_success is not None:
            node["on_success"] = on_success
        if on_failure is not None:
            node["on_failure"] = on_failure
        if max_retries is not None:
            node["max_retries"] = max_retries
        state_raw = {
            "workflow_plan": {"implement": {"nodes": [node]}},
        }
        if retries is not None:
            state_raw["retries"] = retries
        return state_raw

    def test_success_with_on_success_returns_target(self):
        state = self._state_with_edge("step-a", on_success="step-b")
        assert record._resolve_routing("step-a", "completed", state, "implement") == "step-b"

    def test_success_no_edge_returns_advance(self):
        state = self._state_with_edge("step-a")
        assert record._resolve_routing("step-a", "completed", state, "implement") == "advance"

    def test_failure_with_on_failure_returns_target(self):
        state = self._state_with_edge("step-a", on_failure="step-x", max_retries=3)
        assert record._resolve_routing("step-a", "failed", state, "implement") == "step-x"

    def test_abandoned_with_on_failure_returns_target(self):
        state = self._state_with_edge("step-a", on_failure="step-x", max_retries=3)
        assert record._resolve_routing("step-a", "abandoned", state, "implement") == "step-x"

    def test_failure_no_edge_returns_halt(self):
        state = self._state_with_edge("step-a")
        assert record._resolve_routing("step-a", "failed", state, "implement") == "halt"

    def test_failure_halt_keyword_returns_halt(self):
        state = self._state_with_edge("step-a", on_failure="halt", max_retries=3)
        assert record._resolve_routing("step-a", "failed", state, "implement") == "halt"

    def test_failure_retry_cap_escalates_to_halt_cap_exceeded(self):
        state = self._state_with_edge(
            "step-a", on_failure="step-x", max_retries=3,
            retries={"step-a": 3},
        )
        assert record._resolve_routing("step-a", "failed", state, "implement") == record._HALT_CAP_EXCEEDED

    def test_failure_below_cap_increments_retries(self):
        state = self._state_with_edge(
            "step-a", on_failure="step-x", max_retries=3,
            retries={"step-a": 1},
        )
        record._resolve_routing("step-a", "failed", state, "implement")
        assert state["retries"]["step-a"] == 2

    def test_recovered_treated_as_success(self):
        state = self._state_with_edge("step-a", on_success="step-b")
        assert record._resolve_routing("step-a", "recovered", state, "implement") == "step-b"


# ---------------------------------------------------------------------------
# T-4 (RED): end-to-end record() — node re-open on needs_work + escalation
# ---------------------------------------------------------------------------

from orchestrator_next import dispatch  # noqa: E402
from orchestrator_next import readiness  # noqa: E402
from orchestrator_next.parser import load_state  # noqa: E402


_RUN_PHASE_REVIEW_CONTRACT = (
    "id: run-phase-review\n"
    "agent: reviewer\n"
    "instruction: Run phase review.\n"
    "rules: []\n"
    "inputs: []\n"
    "outputs:\n"
    "  - phase_review_report\n"
)


def _nodes_state(tmp_path) -> dict:
    """ORC-63 nodes-shape state.yaml: execute-next-task → run-ux-critique →
    run-phase-review, all `completed` except run-phase-review which is the
    just-completed step being recorded. on_failure loops back to execute-next-task."""
    return {
        "change_id": "orc-67-fixture",
        "phase": "implement",
        "schema": "feature",
        "repo_root": str(tmp_path),
        "worktree_path": str(tmp_path),
        "workflow_plan": {
            "implement": {
                "nodes": [
                    {"id": "execute-next-task", "status": "completed",
                     "agent": "developer", "goal": "", "inputs": [],
                     "outputs": ["task_execution_result"], "rules": []},
                    {"id": "run-ux-critique", "status": "completed",
                     "agent": "ux-critic", "goal": "", "inputs": [],
                     "outputs": ["ux_critique_report"], "rules": []},
                    {"id": "run-phase-review", "status": "in_progress",
                     "on_failure": "execute-next-task",
                     "agent": "reviewer", "goal": "", "inputs": [],
                     "outputs": ["phase_review_report"], "rules": []},
                ],
                "filtered": [],
            }
        },
        "step_history": [
            {"step_id": "run-phase-review", "phase": "implement",
             "status": "in_progress", "agent": "reviewer", "attempt": 1,
             "started_at": "2026-05-22T10:00:00Z", "ended_at": None},
        ],
    }



def _review_payload(verdict: str, retries_count: int | None = None) -> dict:
    """A run-phase-review `done` payload.

    pass → status: completed; needs_work/incomplete_phase → status: failed.
    The reviewer agent emits failed directly (parse-completion.py accepts it).
    retries_count is ignored (engine-owned; kept for back-compat with old callers).
    """
    status = "completed" if verdict == "pass" else "failed"
    return {
        "step_id": "run-phase-review",
        "phase": "implement",
        "status": status,
        "agent": "reviewer",
        "outputs": {"phase_review_report": {"verdict": verdict}},
        # non-zero tokens satisfy record.py's agent-step usage guard.
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def _setup(tmp_path, monkeypatch, state: dict) -> str:
    """Write contract override, tasks.md, state.yaml; return path."""
    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir(exist_ok=True)
    step_dir = contracts_dir / "run-phase-review"
    step_dir.mkdir(exist_ok=True)
    (step_dir / "contract.yaml").write_text(_RUN_PHASE_REVIEW_CONTRACT)
    (step_dir / "prompt.md").write_text("Run phase review.")
    monkeypatch.setenv(
        "ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts_dir)
    )
    # tasks.md with no unchecked items — execute-next-task completes
    # immediately when it is re-opened.
    (tmp_path / "tasks.md").write_text("- [x] T-1: done\n")
    state["tasks_path"] = str(tmp_path / "tasks.md")
    path = tmp_path / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(path)


def _node_status(state_path: str, node_id: str) -> str:
    raw = yaml.safe_load(open(state_path).read())
    nodes = raw["workflow_plan"]["implement"]["nodes"]
    return next(n["status"] for n in nodes if n["id"] == node_id)


class TestReworkRecordNodeReopen:

    def test_needs_work_routes_to_on_failure_target(self, tmp_path, monkeypatch):
        """status: failed + on_failure edge: both run-phase-review and its
        fixer target reset to pending, so the gate re-verifies once the fixer completes."""
        state_path = _setup(tmp_path, monkeypatch, _nodes_state(tmp_path))
        record.record(state_path, _review_payload("needs_work"))

        # run-phase-review is reset (re-runs once execute-next-task completes again)
        assert _node_status(state_path, "run-phase-review") == "reset"
        # on_failure target (execute-next-task) is reset to pending for re-dispatch
        assert _node_status(state_path, "execute-next-task") == "reset"
        # intermediate node (run-ux-critique) is NOT touched
        assert _node_status(state_path, "run-ux-critique") == "completed"

    def test_incomplete_phase_routes_same_as_needs_work(self, tmp_path, monkeypatch):
        """incomplete_phase is also a failure — routes via on_failure."""
        state_path = _setup(tmp_path, monkeypatch, _nodes_state(tmp_path))
        record.record(state_path, _review_payload("incomplete_phase"))

        assert _node_status(state_path, "run-phase-review") == "reset"
        assert _node_status(state_path, "execute-next-task") == "reset"

    def test_pass_verdict_advances_normally(self, tmp_path, monkeypatch):
        """pass verdict: run-phase-review node marked completed, no regression."""
        state_path = _setup(tmp_path, monkeypatch, _nodes_state(tmp_path))
        result, exit_code = record.record(state_path, _review_payload("pass"))

        assert exit_code == 0
        assert _node_status(state_path, "run-phase-review") == "completed"
        # on_failure target is NOT touched on success
        assert _node_status(state_path, "execute-next-task") == "completed"


class TestReworkRecordEscalation:

    def test_ceiling_blocks_and_dispatch_exits_2(self, tmp_path, monkeypatch):
        """needs_work after max_retries exhausted: state.status == blocked;
        dispatch exits 2.

        max_retries=1: first failure routes to on_failure target, second halts.
        """
        state = _nodes_state(tmp_path)
        # Set max_retries=1 on the node (node-level cap, not project.yaml)
        state["workflow_plan"]["implement"]["nodes"][-1]["max_retries"] = 1
        state_path = _setup(tmp_path, monkeypatch, state)
        record.record(state_path, _review_payload("needs_work"))  # retry 1 → routes
        record.record(state_path, _review_payload("needs_work"))  # retry cap hit → halt

        raw = yaml.safe_load(open(state_path).read())
        assert raw.get("status") == "blocked"

        # End-to-end: blocked state makes the next dispatch exit 2.
        _action, exit_code = dispatch.dispatch(load_state(state_path), state_path)
        assert exit_code == 2

    def test_escalation_leaves_review_node_completed(self, tmp_path, monkeypatch):
        """On cap exhaustion run-phase-review is marked completed (not pending)."""
        state = _nodes_state(tmp_path)
        state["workflow_plan"]["implement"]["nodes"][-1]["max_retries"] = 1
        state_path = _setup(tmp_path, monkeypatch, state)
        record.record(state_path, _review_payload("needs_work"))  # retry 1
        record.record(state_path, _review_payload("needs_work"))  # cap hit → halt
        assert _node_status(state_path, "run-phase-review") == "completed"


class TestOnFailureResetReadiness:

    def _state_with_stale_completed_target(self, tmp_path) -> dict:
        state = _nodes_state(tmp_path)
        state["step_history"].append(
            {
                "step_id": "execute-next-task",
                "phase": "implement",
                "status": "completed",
                "agent": "developer",
                "attempt": 1,
                "started_at": "2026-05-22T09:00:00Z",
                "ended_at": "2026-05-22T09:01:00Z",
            }
        )
        return state

    def test_next_ready_node_prefers_reset_target(self, tmp_path, monkeypatch):
        state_raw = self._state_with_stale_completed_target(tmp_path)
        nodes = state_raw["workflow_plan"]["implement"]["nodes"]
        for node in nodes:
            if node["id"] == "execute-next-task":
                node["status"] = "reset"
                break
        state_path = _setup(tmp_path, monkeypatch, state_raw)
        state = load_state(state_path)
        assert readiness.next_ready_node(state) == "execute-next-task"

    def test_record_needs_work_requeues_execute_next_task(self, tmp_path, monkeypatch):
        state_path = _setup(
            tmp_path,
            monkeypatch,
            self._state_with_stale_completed_target(tmp_path),
        )
        record.record(state_path, _review_payload("needs_work"))
        assert _node_status(state_path, "execute-next-task") == "reset"
        updated_state = load_state(state_path)
        assert readiness.next_ready_node(updated_state) == "execute-next-task"

    def test_completed_history_without_explicit_pending_stays_completed(self, tmp_path, monkeypatch):
        state_raw = self._state_with_stale_completed_target(tmp_path)
        state_path = _setup(tmp_path, monkeypatch, state_raw)
        state = load_state(state_path)
        nodes = state.workflow_plan["implement"]["nodes"]
        execute_node = next(node for node in nodes if node["id"] == "execute-next-task")
        assert readiness._effective_node_status(state, execute_node) == "completed"
