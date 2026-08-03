"""review on_success routing — regression for the reset-both bug.

review is the only workflow node with an explicit on_success edge
to a non-adjacent step (ticket-qa). _apply_routing's loop-back branch (added
in 2ff6ade to re-verify failed gates after their fixer) matched on
"routing is a named step_id" without checking success/failure, so a genuine
PASS was reset back to pending instead of marked completed — the workflow
looped review <-> implement forever.
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

_CONTRACT = (
    "id: review\n"
    "agent: reviewer\n"
    "instruction: Run phase review.\n"
    "rules: []\n"
    "inputs: []\n"
    "outputs:\n"
    "  - phase_review_report\n"
)


def _nodes() -> list[dict]:
    return [
        {"id": "implement", "status": "completed", "depends_on": []},
        {
            "id": "review",
            "status": "in_progress",
            "depends_on": ["implement"],
            "on_success": "ticket-qa",
            "on_failure": "implement",
            "max_retries": 8,
        },
        {"id": "ticket-qa", "status": "pending", "depends_on": ["review"]},
    ]


def _state(tmp_path) -> dict:
    return {
        "change_id": "phase-review-routing",
        "phase": "main",
        "schema": "feature",
        "repo_root": str(tmp_path),
        "worktree_path": str(tmp_path),
        "workflow_plan": {"main": {"nodes": _nodes(), "filtered": []}},
        "step_history": [
            {
                "step_id": "review",
                "phase": "main",
                "status": "in_progress",
                "agent": "reviewer",
                "attempt": 1,
                "started_at": "2026-07-13T10:00:00Z",
            },
        ],
    }


def _setup(tmp_path, monkeypatch) -> str:
    contracts_dir = tmp_path / "contracts"
    step_dir = contracts_dir / "review"
    step_dir.mkdir(parents=True, exist_ok=True)
    (step_dir / "contract.yaml").write_text(_CONTRACT)
    (step_dir / "prompt.md").write_text("Run phase review.")
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts_dir))
    path = tmp_path / "state.yaml"
    path.write_text(yaml.safe_dump(_state(tmp_path), sort_keys=False))
    return str(path)


def _node_status(state_path: str, node_id: str) -> str:
    raw = yaml.safe_load(open(state_path).read())
    nodes = raw["workflow_plan"]["main"]["nodes"]
    return next(n["status"] for n in nodes if n["id"] == node_id)


def _payload(*, status: str, verdict: str) -> dict:
    return {
        "step_id": "review",
        "phase": "main",
        "status": status,
        "agent": "reviewer",
        "outputs": {"reason": "test", "phase_review_report": {"verdict": verdict}},
        "review_score": {
            "overall": 10,
            "dimensions": {
                "spec_compliance": 10, "correctness": 10, "security": 10,
                "simplicity": 10, "code_quality": 10,
            },
        },
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


class TestPhaseReviewSuccessRouting:

    def test_pass_marks_step_completed_not_reset(self, tmp_path, monkeypatch):
        state_path = _setup(tmp_path, monkeypatch)
        result, code = record.record(
            state_path, _payload(status="completed", verdict="pass")
        )
        assert code == 0, result
        assert _node_status(state_path, "review") == "completed"

    def test_pass_advances_to_ticket_qa(self, tmp_path, monkeypatch):
        state_path = _setup(tmp_path, monkeypatch)
        record.record(state_path, _payload(status="completed", verdict="pass"))
        state = load_state(state_path)
        assert readiness.next_ready_node(state) == "ticket-qa"

    def test_needs_work_still_resets_both_and_requeues_implement(
        self, tmp_path, monkeypatch
    ):
        """The on_failure loop-back this bug's fix (2ff6ade) protects must
        still work: a real failure resets both nodes and requeues the fixer."""
        state_path = _setup(tmp_path, monkeypatch)
        record.record(
            state_path, _payload(status="failed", verdict="needs_work")
        )
        assert _node_status(state_path, "review") == "reset"
        assert _node_status(state_path, "implement") == "reset"
        state = load_state(state_path)
        assert readiness.next_ready_node(state) == "implement"
