"""design-review on_failure routing — regression for ORC-117 / ORC-119 class bugs.

When design-review fails (needs_work), the engine must re-queue
design-and-draft-artifacts — not advance to ticket-start / implement.

Covers the agent mistake where status: completed is emitted alongside
design_review_result: needs_work (routing keys off status, not the output).
"""
from __future__ import annotations

import os
import sys

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from orchestrator_next import readiness, record  # noqa: E402
from orchestrator_next.parser import load_state  # noqa: E402

_DESIGN_REVIEW_CONTRACT = (
    "id: design-review\n"
    "agent: reviewer\n"
    "instruction: Run design review.\n"
    "rules: []\n"
    "inputs: []\n"
    "outputs:\n"
    "  - design_review_result\n"
)


def _feature_nodes_at_design_review() -> list[dict]:
    """Minimal feature DAG at design-review with explicit depends_on edges."""
    return [
        {"id": "explore", "status": "completed", "depends_on": []},
        {"id": "ux-design", "status": "completed", "depends_on": ["explore"]},
        {
            "id": "design-and-draft-artifacts",
            "status": "completed",
            "depends_on": ["ux-design"],
        },
        {
            "id": "design-review",
            "status": "in_progress",
            "depends_on": ["design-and-draft-artifacts"],
            "on_failure": "design-and-draft-artifacts",
            "max_retries": 3,
        },
        {"id": "ticket-start", "status": "pending", "depends_on": ["design-review"]},
    ]


def _state_at_design_review(tmp_path, *, with_stale_architect_history: bool = False) -> dict:
    state: dict = {
        "change_id": "design-review-routing",
        "phase": "main",
        "schema": "feature",
        "repo_root": str(tmp_path),
        "worktree_path": str(tmp_path),
        "workflow_plan": {"main": {"nodes": _feature_nodes_at_design_review(), "filtered": []}},
        "step_history": [
            {
                "step_id": "design-review",
                "phase": "main",
                "status": "in_progress",
                "agent": "reviewer",
                "attempt": 1,
                "started_at": "2026-07-07T10:00:00Z",
            },
        ],
    }
    if with_stale_architect_history:
        state["step_history"].insert(
            0,
            {
                "step_id": "design-and-draft-artifacts",
                "phase": "main",
                "status": "completed",
                "agent": "architect",
                "attempt": 1,
                "started_at": "2026-07-07T09:00:00Z",
                "ended_at": "2026-07-07T09:30:00Z",
            },
        )
    return state


def _design_review_payload(*, status: str, result: str) -> dict:
    return {
        "step_id": "design-review",
        "phase": "main",
        "status": status,
        "agent": "reviewer",
        "outputs": {"design_review_result": result},
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def _setup(tmp_path, monkeypatch, state: dict) -> str:
    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir(exist_ok=True)
    step_dir = contracts_dir / "design-review"
    step_dir.mkdir(exist_ok=True)
    (step_dir / "contract.yaml").write_text(_DESIGN_REVIEW_CONTRACT)
    (step_dir / "prompt.md").write_text("Run design review.")
    monkeypatch.setenv(
        "ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts_dir)
    )
    path = tmp_path / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(path)


def _node_status(state_path: str, node_id: str) -> str:
    raw = yaml.safe_load(open(state_path).read())
    nodes = raw["workflow_plan"]["main"]["nodes"]
    return next(n["status"] for n in nodes if n["id"] == node_id)


class TestDesignReviewFailureRouting:

    def test_failed_needs_work_requeues_architect(self, tmp_path, monkeypatch):
        state_path = _setup(tmp_path, monkeypatch, _state_at_design_review(tmp_path))
        result, code = record.record(
            state_path, _design_review_payload(status="failed", result="needs_work")
        )
        assert code == 0, result
        assert _node_status(state_path, "design-review") == "completed"
        assert _node_status(state_path, "design-and-draft-artifacts") == "reset"
        state = load_state(state_path)
        assert readiness.next_ready_node(state) == "design-and-draft-artifacts"

    def test_completed_needs_work_coerced_to_failed_and_requeues_architect(
        self, tmp_path, monkeypatch
    ):
        """Agent mistake: status completed + needs_work must not advance to implement."""
        state_path = _setup(tmp_path, monkeypatch, _state_at_design_review(tmp_path))
        result, code = record.record(
            state_path, _design_review_payload(status="completed", result="needs_work")
        )
        assert code == 0, result
        raw = yaml.safe_load(open(state_path).read())
        assert raw["step_history"][-1]["status"] == "failed"
        assert _node_status(state_path, "design-and-draft-artifacts") == "reset"
        state = load_state(state_path)
        assert readiness.next_ready_node(state) == "design-and-draft-artifacts"
        assert readiness.next_ready_node(state) != "ticket-start"

    def test_completed_pass_advances_to_ticket_start(self, tmp_path, monkeypatch):
        state_path = _setup(tmp_path, monkeypatch, _state_at_design_review(tmp_path))
        result, code = record.record(
            state_path, _design_review_payload(status="completed", result="pass")
        )
        assert code == 0, result
        state = load_state(state_path)
        assert readiness.next_ready_node(state) == "ticket-start"

    def test_stale_architect_history_still_requeues_on_failure(self, tmp_path, monkeypatch):
        """ORC-119: step_history completed entry must not block on_failure reset."""
        state_path = _setup(
            tmp_path,
            monkeypatch,
            _state_at_design_review(tmp_path, with_stale_architect_history=True),
        )
        record.record(
            state_path, _design_review_payload(status="failed", result="needs_work")
        )
        state = load_state(state_path)
        assert readiness.next_ready_node(state) == "design-and-draft-artifacts"

    def test_completed_without_pass_result_rejected(self, tmp_path, monkeypatch):
        state_path = _setup(tmp_path, monkeypatch, _state_at_design_review(tmp_path))
        payload = _design_review_payload(status="completed", result="pass")
        del payload["outputs"]["design_review_result"]
        result, code = record.record(state_path, payload)
        assert code == 3
        assert result["reason"] == "invalid_design_review_result"
