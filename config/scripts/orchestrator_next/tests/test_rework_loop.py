"""orc-67: run-phase-review needs_work rework loop.

T-2 (RED): unit tests for the three pure helpers — `_payload_phase_review_verdict`,
`_rework_loop_active`, `_max_retry_rounds`. They fail today because the symbols
do not exist in record.py yet.

Note: `record.py` already has an entry-time `_phase_review_verdict(entry)` used
by `extract_review_scores` (reads `entry.evidence.outputs`). The rework loop
needs a *payload-time* extractor (reads `payload.outputs` directly and gates on
`step_id`), so it is named distinctly to avoid breaking that existing helper.

T-4 (RED) extends this module with end-to-end `record()` cases for node
re-open and ceiling escalation.
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

from orchestrator_next import record  # noqa: E402


# ---------------------------------------------------------------------------
# _payload_phase_review_verdict
# ---------------------------------------------------------------------------

class TestPayloadPhaseReviewVerdict:

    def _payload(self, step_id: str, report) -> dict:
        outputs = {} if report is None else {"phase_review_report": report}
        return {"step_id": step_id, "phase": "implement", "outputs": outputs}

    def test_returns_verdict_for_run_phase_review_payload(self):
        payload = self._payload("run-phase-review", {"verdict": "needs_work"})
        assert record._payload_phase_review_verdict(payload) == "needs_work"

    def test_returns_pass_verdict(self):
        payload = self._payload("run-phase-review", {"verdict": "pass"})
        assert record._payload_phase_review_verdict(payload) == "pass"

    def test_returns_none_for_other_step_ids(self):
        payload = self._payload("execute-next-task", {"verdict": "needs_work"})
        assert record._payload_phase_review_verdict(payload) is None

    def test_returns_none_when_report_absent(self):
        payload = self._payload("run-phase-review", None)
        assert record._payload_phase_review_verdict(payload) is None

    def test_returns_none_when_report_not_a_dict(self):
        payload = self._payload("run-phase-review", "not-a-dict")
        assert record._payload_phase_review_verdict(payload) is None

    def test_returns_none_when_outputs_missing(self):
        payload = {"step_id": "run-phase-review", "phase": "implement"}
        assert record._payload_phase_review_verdict(payload) is None


# ---------------------------------------------------------------------------
# _rework_loop_active
# ---------------------------------------------------------------------------

class TestReworkLoopActive:

    def test_retry_for_needs_work_below_ceiling(self):
        assert record._rework_loop_active(
            "needs_work", {"run-phase-review": 2}, 8
        ) == "retry"

    def test_retry_for_incomplete_phase_below_ceiling(self):
        assert record._rework_loop_active(
            "incomplete_phase", {"run-phase-review": 0}, 8
        ) == "retry"

    def test_escalate_at_ceiling(self):
        assert record._rework_loop_active(
            "needs_work", {"run-phase-review": 8}, 8
        ) == "escalate"

    def test_escalate_above_ceiling(self):
        assert record._rework_loop_active(
            "needs_work", {"run-phase-review": 9}, 8
        ) == "escalate"

    def test_none_for_pass_verdict(self):
        assert record._rework_loop_active(
            "pass", {"run-phase-review": 2}, 8
        ) is None

    def test_none_for_unknown_verdict(self):
        assert record._rework_loop_active(
            "something-else", {"run-phase-review": 2}, 8
        ) is None

    def test_retries_absent_treated_as_zero(self):
        # retries dict has no run-phase-review key → count 0 → retry
        assert record._rework_loop_active("needs_work", {}, 8) == "retry"

    def test_retries_none_treated_as_zero(self):
        # retries is None → count 0, no raise
        assert record._rework_loop_active("needs_work", None, 8) == "retry"

    def test_retries_not_a_dict_treated_as_zero(self):
        # retries is a malformed non-dict → count 0, no raise
        assert record._rework_loop_active("needs_work", ["bad"], 8) == "retry"


# ---------------------------------------------------------------------------
# _max_retry_rounds
# ---------------------------------------------------------------------------

class TestMaxRetryRounds:

    def _write_project_yaml(self, root, content) -> None:
        spec_dir = root / "spec"
        spec_dir.mkdir(parents=True, exist_ok=True)
        (spec_dir / "project.yaml").write_text(yaml.safe_dump(content))

    def test_reads_max_retry_rounds_from_project_yaml(self, tmp_path):
        self._write_project_yaml(tmp_path, {"quality_bar": {"max_retry_rounds": 8}})
        state_raw = {"repo_root": str(tmp_path)}
        assert record._max_retry_rounds(state_raw) == 8

    def test_reads_from_worktree_path_when_present(self, tmp_path):
        self._write_project_yaml(tmp_path, {"quality_bar": {"max_retry_rounds": 5}})
        state_raw = {"worktree_path": str(tmp_path), "repo_root": "/nonexistent"}
        assert record._max_retry_rounds(state_raw) == 5

    def test_default_3_with_warning_when_key_absent(self, tmp_path, capsys):
        self._write_project_yaml(tmp_path, {"quality_bar": {}})
        state_raw = {"repo_root": str(tmp_path)}
        assert record._max_retry_rounds(state_raw) == 3
        assert "[record]" in capsys.readouterr().err

    def test_default_3_with_warning_when_project_yaml_missing(self, tmp_path, capsys):
        # no project.yaml written at all
        state_raw = {"repo_root": str(tmp_path)}
        assert record._max_retry_rounds(state_raw) == 3
        assert "[record]" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# T-4 (RED): end-to-end record() — node re-open on needs_work + escalation
# ---------------------------------------------------------------------------

from orchestrator_next import dispatch  # noqa: E402
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
    just-completed step being recorded."""
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


def _legacy_active_state(tmp_path) -> dict:
    """Legacy `active:[ids]` plan shape (pre-ORC-63) — no node dicts."""
    return {
        "change_id": "orc-67-legacy",
        "phase": "implement",
        "schema": "feature",
        "repo_root": str(tmp_path),
        "worktree_path": str(tmp_path),
        "workflow_plan": {
            "implement": {
                "active": ["execute-next-task", "run-phase-review"],
            }
        },
        "step_history": [],
    }


def _review_payload(verdict: str, retries_count: int | None = None) -> dict:
    """A run-phase-review `done` payload with the given verdict."""
    payload = {
        "step_id": "run-phase-review",
        "phase": "implement",
        "status": "completed",
        "agent": "reviewer",
        "outputs": {"phase_review_report": {"verdict": verdict}},
        # non-zero tokens satisfy record.py's agent-step usage guard.
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    if retries_count is not None:
        payload["state_patch"] = {"retries": {"run-phase-review": retries_count}}
    return payload


def _setup(tmp_path, monkeypatch, state: dict, max_retry_rounds: int = 8) -> str:
    """Write contract override, project.yaml, tasks.md, state.yaml; return path."""
    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir(exist_ok=True)
    (contracts_dir / "run-phase-review.yaml").write_text(_RUN_PHASE_REVIEW_CONTRACT)
    monkeypatch.setenv(
        "ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts_dir)
    )
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir(exist_ok=True)
    (spec_dir / "project.yaml").write_text(
        yaml.safe_dump({"quality_bar": {"max_retry_rounds": max_retry_rounds}})
    )
    # tasks.md with no unchecked items — execute-next-task's repeat_until is
    # satisfied immediately when it is re-opened.
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

    def test_needs_work_below_ceiling_reopens_review_node_only(self, tmp_path, monkeypatch):
        """needs_work + retries < max: ORC-65: record.py only resets run-phase-review
        to in_progress. execute-next-task stays completed. Fix task-nodes are injected
        by the agent calling `orchestrator expand-plan` before returning COMPLETION
        — record.py has no execute-next-task special-case reset."""
        state_path = _setup(tmp_path, monkeypatch, _nodes_state(tmp_path))
        record.record(state_path, _review_payload("needs_work", retries_count=1))

        assert _node_status(state_path, "run-phase-review") == "in_progress"
        # ORC-65: execute-next-task is NOT reset — the agent injected fix-task nodes
        # via expand-plan before returning COMPLETION.
        assert _node_status(state_path, "execute-next-task") == "completed"
        assert _node_status(state_path, "run-ux-critique") == "completed", (
            "intermediate node must not be re-dispatched"
        )

    def test_incomplete_phase_behaves_like_needs_work(self, tmp_path, monkeypatch):
        """incomplete_phase + retries remaining: same as needs_work — only
        run-phase-review is reset to in_progress."""
        state_path = _setup(tmp_path, monkeypatch, _nodes_state(tmp_path))
        record.record(state_path, _review_payload("incomplete_phase", retries_count=2))

        assert _node_status(state_path, "run-phase-review") == "in_progress"
        # ORC-65: execute-next-task stays completed.
        assert _node_status(state_path, "execute-next-task") == "completed"

    def test_pass_verdict_advances_normally(self, tmp_path, monkeypatch):
        """pass verdict: run-phase-review node marked completed, no regression."""
        state_path = _setup(tmp_path, monkeypatch, _nodes_state(tmp_path))
        result, exit_code = record.record(state_path, _review_payload("pass"))

        assert exit_code == 0
        assert _node_status(state_path, "run-phase-review") == "completed"
        # next_step does not point back at execute-next-task
        next_step = (result.get("next_step") or {}).get("step_id")
        assert next_step != "execute-next-task"

    def test_needs_work_review_reset_bounded(self, tmp_path, monkeypatch):
        """needs_work: record.py resets only run-phase-review to in_progress.
        ORC-65: execute-next-task is NOT reset; bounded by ceiling as before."""
        state_path = _setup(tmp_path, monkeypatch, _nodes_state(tmp_path))
        record.record(state_path, _review_payload("needs_work", retries_count=0))
        # ORC-65: only run-phase-review is reset; execute-next-task stays completed.
        assert _node_status(state_path, "execute-next-task") == "completed"
        assert _node_status(state_path, "run-phase-review") == "in_progress"

    def test_legacy_active_plan_degrades_without_error(self, tmp_path, monkeypatch):
        """Legacy active:[ids] plan: needs_work record degrades to linear
        advance — mark_node_status is a no-op on a node-less plan, no exception."""
        state_path = _setup(tmp_path, monkeypatch, _legacy_active_state(tmp_path))
        result, exit_code = record.record(
            state_path, _review_payload("needs_work", retries_count=1)
        )
        assert exit_code == 0  # no exception raised
        raw = yaml.safe_load(open(state_path).read())
        # legacy plan has no nodes list — still an active block, untouched
        assert "nodes" not in raw["workflow_plan"]["implement"]


class TestReworkRecordEscalation:

    def test_ceiling_blocks_and_pauses_and_dispatch_exits_2(self, tmp_path, monkeypatch):
        """needs_work + retries >= max: recorded step_history entry has
        status blocked; state.status == 'paused'; dispatch.dispatch() on the
        post-record state exits 2."""
        state_path = _setup(
            tmp_path, monkeypatch, _nodes_state(tmp_path), max_retry_rounds=8
        )
        record.record(state_path, _review_payload("needs_work", retries_count=8))

        raw = yaml.safe_load(open(state_path).read())
        last = raw["step_history"][-1]
        assert last["step_id"] == "run-phase-review"
        assert last["status"] == "blocked", (
            "retry-exhausted review must be downgraded to blocked"
        )
        assert raw.get("status") == "paused"

        # End-to-end: the blocked last entry makes the next dispatch exit 2.
        _action, exit_code = dispatch.dispatch(load_state(state_path), state_path)
        assert exit_code == 2

    def test_escalation_leaves_nodes_completed(self, tmp_path, monkeypatch):
        """On ceiling escalation no nodes are re-opened — the loop is terminated."""
        state_path = _setup(
            tmp_path, monkeypatch, _nodes_state(tmp_path), max_retry_rounds=8
        )
        record.record(state_path, _review_payload("needs_work", retries_count=8))
        assert _node_status(state_path, "run-phase-review") == "completed"
        assert _node_status(state_path, "execute-next-task") == "completed"
