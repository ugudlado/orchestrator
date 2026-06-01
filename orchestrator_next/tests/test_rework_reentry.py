"""orc-103 RED: readiness-layer verdict-aware completion for rework re-entry.

Pins the bug at readiness._effective_node_status / next_ready_node: a
run-phase-review step_history entry with status=completed and a needs_work
verdict must NOT make the node terminal when the plan re-arms it to in_progress.

These tests fail on HEAD because _step_completed_in_history counts any
completed entry as terminal (ORC-85 history-authoritative completion).
"""
from __future__ import annotations

import importlib.util
import os
import sys

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from orchestrator_next import dispatch, readiness, record  # noqa: E402
from orchestrator_next.parser import load_state, phase_nodes  # noqa: E402

_loop_spec = importlib.util.spec_from_file_location(
    "orchestrator_test_rework_loop",
    os.path.join(_HERE, "test_rework_loop.py"),
)
_rework_loop = importlib.util.module_from_spec(_loop_spec)
assert _loop_spec.loader is not None
_loop_spec.loader.exec_module(_rework_loop)
_nodes_state = _rework_loop._nodes_state
_review_payload = _rework_loop._review_payload
_record_setup = _rework_loop._setup


# ---------------------------------------------------------------------------
# Fixtures / helpers (shape from test_rework_loop._nodes_state and
# test_dispatch_retry_storm._promoted_plan_state)
# ---------------------------------------------------------------------------


def _setup(tmp_path, monkeypatch, state: dict) -> str:
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir(exist_ok=True)
    (spec_dir / "project.yaml").write_text(
        yaml.safe_dump({"quality_bar": {"max_retry_rounds": 8}})
    )
    state.setdefault("repo_root", str(tmp_path))
    state.setdefault("worktree_path", str(tmp_path))
    path = tmp_path / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(path)


def _phase_review_history_entry(
    *,
    verdict: str | None,
    attempt: int = 1,
    status: str = "completed",
    phase: str = "implement",
) -> dict:
    """History entry as record.py writes it (verdict nested under evidence.outputs)."""
    entry: dict = {
        "step_id": "run-phase-review",
        "phase": phase,
        "status": status,
        "agent": "reviewer",
        "attempt": attempt,
        "started_at": f"2026-05-29T10:00:{attempt:02d}Z",
        "ended_at": f"2026-05-29T10:05:{attempt:02d}Z",
    }
    if verdict is not None:
        entry["evidence"] = {
            "outputs": {"phase_review_report": {"verdict": verdict}},
        }
    return entry


def _malformed_review_history_entry() -> dict:
    """Completed review entry with unreadable verdict (fail-safe case)."""
    return {
        "step_id": "run-phase-review",
        "phase": "implement",
        "status": "completed",
        "agent": "reviewer",
        "attempt": 1,
        "started_at": "2026-05-29T10:00:00Z",
        "ended_at": "2026-05-29T10:05:00Z",
        "evidence": "not-a-dict",
    }


def _plain_node(
    node_id: str,
    *,
    status: str = "pending",
    depends_on: list[str] | None = None,
    agent: str = "developer",
    outputs: list[str] | None = None,
) -> dict:
    return {
        "id": node_id,
        "status": status,
        "agent": agent,
        "goal": "",
        "inputs": [],
        "outputs": outputs or ["task_execution_result"],
        "rules": [],
        "depends_on": depends_on or [],
    }


def _rework_dag_state(
    tmp_path,
    *,
    review_status: str = "in_progress",
    review_depends_on: list[str] | None = None,
    step_history: list[dict],
    phase: str = "implement",
) -> dict:
    """DAG: execute-next-task → task-fix-1 → run-phase-review → run-learn-cycle."""
    return {
        "change_id": "orc-103-fixture",
        "phase": phase,
        "schema": "feature",
        "repo_root": str(tmp_path),
        "worktree_path": str(tmp_path),
        "workflow_plan": {
            phase: {
                "nodes": [
                    _plain_node("execute-next-task", status="completed"),
                    _plain_node(
                        "task-fix-1",
                        status="completed",
                        depends_on=["execute-next-task"],
                    ),
                    _plain_node(
                        "run-phase-review",
                        status=review_status,
                        depends_on=review_depends_on or ["task-fix-1"],
                        agent="reviewer",
                        outputs=["phase_review_report"],
                    ),
                    _plain_node(
                        "run-learn-cycle",
                        status="pending",
                        depends_on=["run-phase-review"],
                        agent=None,
                        outputs=[],
                    ),
                ],
                "filtered": [],
            }
        },
        "step_history": step_history,
    }


def _review_node(state) -> dict:
    nodes = phase_nodes(state, state.phase)
    return next(n for n in nodes if n["id"] == "run-phase-review")


# ---------------------------------------------------------------------------
# AC-4: _effective_node_status — verdict-aware completion inference
# ---------------------------------------------------------------------------


class TestEffectiveNodeStatusVerdictAware:

    def test_needs_work_completed_history_on_in_progress_node_returns_in_progress(
        self, tmp_path, monkeypatch,
    ):
        """AC-4 live bug: needs_work history must not override in_progress re-arm."""
        state_raw = _rework_dag_state(
            tmp_path,
            review_status="in_progress",
            step_history=[_phase_review_history_entry(verdict="needs_work")],
        )
        state_path = _setup(tmp_path, monkeypatch, state_raw)
        state = load_state(state_path)

        assert readiness._effective_node_status(state, _review_node(state)) == (
            "in_progress"
        )

    def test_recovered_history_on_in_progress_node_returns_completed(
        self, tmp_path, monkeypatch,
    ):
        """AC-4 crash-resume: recovered entry still terminates (ORC-85 preserved)."""
        state_raw = _rework_dag_state(
            tmp_path,
            review_status="in_progress",
            step_history=[
                {
                    "step_id": "run-phase-review",
                    "phase": "implement",
                    "status": "recovered",
                    "agent": "reviewer",
                    "attempt": 1,
                    "started_at": "2026-05-29T10:00:00Z",
                    "ended_at": "2026-05-29T10:05:00Z",
                },
            ],
        )
        state_path = _setup(tmp_path, monkeypatch, state_raw)
        state = load_state(state_path)

        assert readiness._effective_node_status(state, _review_node(state)) == (
            "completed"
        )

    def test_incomplete_phase_completed_history_is_non_terminal(
        self, tmp_path, monkeypatch,
    ):
        """AC-4: incomplete_phase behaves like needs_work."""
        state_raw = _rework_dag_state(
            tmp_path,
            review_status="in_progress",
            step_history=[_phase_review_history_entry(verdict="incomplete_phase")],
        )
        state_path = _setup(tmp_path, monkeypatch, state_raw)
        state = load_state(state_path)

        assert readiness._effective_node_status(state, _review_node(state)) == (
            "in_progress"
        )

    def test_pass_completed_history_is_terminal(self, tmp_path, monkeypatch):
        """AC-4: pass verdict terminates the node."""
        state_raw = _rework_dag_state(
            tmp_path,
            review_status="completed",
            step_history=[_phase_review_history_entry(verdict="pass")],
        )
        state_path = _setup(tmp_path, monkeypatch, state_raw)
        state = load_state(state_path)

        assert readiness._effective_node_status(state, _review_node(state)) == (
            "completed"
        )

    def test_multi_round_history_trailing_pass_wins(self, tmp_path, monkeypatch):
        """Earlier needs_work entries non-terminal; trailing pass terminates."""
        state_raw = _rework_dag_state(
            tmp_path,
            review_status="completed",
            step_history=[
                _phase_review_history_entry(verdict="needs_work", attempt=1),
                _phase_review_history_entry(verdict="needs_work", attempt=2),
                _phase_review_history_entry(verdict="pass", attempt=3),
            ],
        )
        state_path = _setup(tmp_path, monkeypatch, state_raw)
        state = load_state(state_path)

        assert readiness._effective_node_status(state, _review_node(state)) == (
            "completed"
        )

    def test_malformed_evidence_treated_as_terminal(self, tmp_path, monkeypatch):
        """Fail-safe: unreadable verdict → terminal (do not block forever)."""
        state_raw = _rework_dag_state(
            tmp_path,
            review_status="in_progress",
            step_history=[_malformed_review_history_entry()],
        )
        state_path = _setup(tmp_path, monkeypatch, state_raw)
        state = load_state(state_path)

        assert readiness._effective_node_status(state, _review_node(state)) == (
            "completed"
        )


# ---------------------------------------------------------------------------
# AC-1 / AC-2: next_ready_node end-to-end DAG walk
# ---------------------------------------------------------------------------


class TestNextReadyNodeReworkReentry:

    def test_after_fix_task_next_ready_is_run_phase_review_not_compute(
        self, tmp_path, monkeypatch,
    ):
        """AC-1: re-armed review with needs_work history re-enters review, not compute."""
        state_raw = _rework_dag_state(
            tmp_path,
            review_status="in_progress",
            review_depends_on=["task-fix-1"],
            step_history=[_phase_review_history_entry(verdict="needs_work")],
        )
        state_path = _setup(tmp_path, monkeypatch, state_raw)
        state = load_state(state_path)

        assert readiness.next_ready_node(state) == "run-phase-review"
        assert readiness.next_ready_node(state) != "run-learn-cycle"

    def test_after_pass_review_next_ready_is_compute(self, tmp_path, monkeypatch):
        """AC-2: pass verdict advances to run-learn-cycle."""
        state_raw = _rework_dag_state(
            tmp_path,
            review_status="completed",
            review_depends_on=["task-fix-1"],
            step_history=[
                _phase_review_history_entry(verdict="needs_work", attempt=1),
                _phase_review_history_entry(verdict="pass", attempt=2),
            ],
        )
        state_path = _setup(tmp_path, monkeypatch, state_raw)
        state = load_state(state_path)

        assert readiness.next_ready_node(state) == "run-learn-cycle"


# ---------------------------------------------------------------------------
# T-4: end-to-end record() — engine-owned retry counter + cap escalation
# ---------------------------------------------------------------------------


def _nodes_state_with_compute(tmp_path) -> dict:
    """_nodes_state plus downstream compute node (for dispatch non-advance checks)."""
    state = _nodes_state(tmp_path)
    state["workflow_plan"]["implement"]["nodes"].append(
        {
            "id": "run-learn-cycle",
            "status": "pending",
            "depends_on": ["run-phase-review"],
            "agent": None,
            "goal": "",
            "inputs": [],
            "outputs": [],
            "rules": [],
        }
    )
    return state


def _state_retries(state_path: str) -> int:
    raw = yaml.safe_load(open(state_path).read())
    retries = raw.get("retries") or {}
    return int(retries.get("run-phase-review", 0))


def _node_status(state_path: str, node_id: str) -> str:
    raw = yaml.safe_load(open(state_path).read())
    nodes = raw["workflow_plan"]["implement"]["nodes"]
    return next(n["status"] for n in nodes if n["id"] == node_id)


class TestReworkRecordCounterClimb:
    """AC-1: engine increments retries — payloads must not supply state_patch.retries."""

    def test_needs_work_counter_climbs_from_engine_not_payload(
        self, tmp_path, monkeypatch,
    ):
        state_path = _record_setup(
            tmp_path, monkeypatch, _nodes_state(tmp_path), max_retry_rounds=8
        )
        assert _state_retries(state_path) == 0

        for expected in (1, 2, 3):
            record.record(state_path, _review_payload("needs_work"))
            assert _state_retries(state_path) == expected

    def test_below_cap_does_not_block_or_pause(self, tmp_path, monkeypatch):
        """AC-3: retries below max + needs_work → completed entry, in_progress node, active."""
        state_path = _record_setup(
            tmp_path, monkeypatch, _nodes_state(tmp_path), max_retry_rounds=8
        )
        record.record(state_path, _review_payload("needs_work"))

        raw = yaml.safe_load(open(state_path).read())
        last = raw["step_history"][-1]
        assert last["step_id"] == "run-phase-review"
        assert last["status"] == "completed"
        assert raw.get("status") != "paused"
        assert _node_status(state_path, "run-phase-review") == "in_progress"


class TestReworkRecordExhaustion:
    """AC-3: cap exhaustion blocks, pauses, and dispatch exits 2."""

    def test_max_retries_exhaustion_blocks_pauses_dispatch_exits_2(
        self, tmp_path, monkeypatch,
    ):
        max_rounds = 3
        state_path = _record_setup(
            tmp_path,
            monkeypatch,
            _nodes_state_with_compute(tmp_path),
            max_retry_rounds=max_rounds,
        )

        for round_idx in range(1, max_rounds + 1):
            record.record(state_path, _review_payload("needs_work"))
            raw = yaml.safe_load(open(state_path).read())
            assert raw.get("status") != "paused"
            assert _state_retries(state_path) == round_idx
            assert raw["step_history"][-1]["status"] == "completed"

        assert _state_retries(state_path) == max_rounds

        record.record(state_path, _review_payload("needs_work"))

        raw = yaml.safe_load(open(state_path).read())
        last = raw["step_history"][-1]
        assert last["step_id"] == "run-phase-review"
        assert last["status"] == "blocked"
        assert raw.get("status") == "paused"

        # End-to-end: blocked last entry halts dispatch (no spawn of compute).
        state = load_state(state_path)
        action, exit_code = dispatch.dispatch(state, state_path)
        assert exit_code == 2
        assert action == {}


# ---------------------------------------------------------------------------
# fix-1: record() history entry through _effective_node_status / next_ready_node
# ---------------------------------------------------------------------------


class TestReworkRecordComposition:
    """F-1: real record()-produced needs_work entry via next_ready_node (orc-96 surface)."""

    def test_real_needs_work_record_reenters_then_pass_advances(
        self, tmp_path, monkeypatch,
    ):
        """record() needs_work → re-entry at review; pass → advance to compute."""
        state_path = _record_setup(
            tmp_path,
            monkeypatch,
            _nodes_state_with_compute(tmp_path),
            max_retry_rounds=8,
        )

        record.record(state_path, _review_payload("needs_work"))
        state = load_state(state_path)
        assert readiness.next_ready_node(state) == "run-phase-review"
        assert readiness.next_ready_node(state) != "run-learn-cycle"

        record.record(state_path, _review_payload("pass"))
        state = load_state(state_path)
        assert readiness.next_ready_node(state) == "run-learn-cycle"
