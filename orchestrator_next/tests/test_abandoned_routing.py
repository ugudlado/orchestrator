"""Abandoned routing: an abandoned judgment step must not read as completed.

Live run 01a0af00-acad-75ab-b650-7025a6a952db: `design` recorded
`--status abandoned` (the agent refused to invent scope, wrote no design.md /
tasks.yaml). record.py flipped the node to `completed` on the halt path, so
readiness treated `design-review`'s dependency as satisfied and dispatched it
against artifacts that did not exist.

Expected behaviour:
  - abandoned with no on_failure edge -> node status `abandoned` (terminal,
    never re-dispatched) and state.status `needs_you`.
  - dependents of an abandoned node are NOT ready.
  - abandoned with an on_failure edge -> that edge routes, bounded by
    max_retries exactly like needs_work.
"""
from __future__ import annotations

import pytest
import yaml

from orchestrator_next import readiness
from orchestrator_next.parser import load_state
from orchestrator_next.record import record


def _write_state(tmp_path, nodes, phase="main"):
    state = {
        "change_id": "abandon-test",
        "phase": phase,
        "schema": "feature",
        "workflow_plan": {phase: {"nodes": nodes, "filtered": []}},
        "step_history": [],
    }
    path = tmp_path / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(path)


@pytest.fixture(autouse=True)
def isolate_contracts(tmp_path, monkeypatch):
    empty = tmp_path / "empty_contracts"
    empty.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(empty))


def _record(state_path, step_id, status, phase="main", outputs=None):
    return record(
        state_path,
        {
            "step_id": step_id,
            "phase": phase,
            "status": status,
            "agent": "strong",
            "outputs": outputs or {"reason": "no designable scope"},
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    )


class TestAbandonedIsNotCompleted:
    def test_abandoned_node_status_is_abandoned_not_completed(self, tmp_path):
        path = _write_state(tmp_path, [
            {"id": "design", "status": "in_progress", "depends_on": []},
            {"id": "design-review", "status": "pending", "depends_on": ["design"]},
        ])
        _record(path, "design", "abandoned")
        raw = yaml.safe_load(open(path).read())
        node = raw["workflow_plan"]["main"]["nodes"][0]
        assert node["status"] == "abandoned", (
            f"expected terminal 'abandoned', got {node['status']!r} — "
            "'completed' would make dependents ready against missing artifacts"
        )

    def test_dependents_of_abandoned_node_are_not_ready(self, tmp_path):
        path = _write_state(tmp_path, [
            {"id": "design", "status": "in_progress", "depends_on": []},
            {"id": "design-review", "status": "pending", "depends_on": ["design"]},
        ])
        _record(path, "design", "abandoned")
        state = load_state(path)
        assert not readiness.is_node_ready(state, "design-review"), (
            "design-review must not be ready when design was abandoned"
        )

    def test_abandoned_node_is_not_re_dispatched(self, tmp_path):
        """ORC-75: the abandoned node itself must stay terminal, not loop."""
        path = _write_state(tmp_path, [
            {"id": "design", "status": "in_progress", "depends_on": []},
        ])
        _record(path, "design", "abandoned")
        state = load_state(path)
        assert not readiness.is_node_ready(state, "design")
        assert readiness.next_ready_node(state) is None

    def test_abandoned_sets_state_needs_you_with_reason(self, tmp_path):
        path = _write_state(tmp_path, [
            {"id": "design", "status": "in_progress", "depends_on": []},
        ])
        _record(path, "design", "abandoned",
                outputs={"reason": "no designable scope in this repo"})
        raw = yaml.safe_load(open(path).read())
        assert raw["status"] == "needs_you"
        assert "design abandoned" in str(raw.get("needs_you_reason") or "")
        assert "no designable scope" in str(raw.get("needs_you_reason") or "")


class TestAbandonedHonoursOnFailure:
    def test_abandoned_routes_via_on_failure(self, tmp_path):
        path = _write_state(tmp_path, [
            {"id": "explore", "status": "completed", "depends_on": []},
            {"id": "design", "status": "in_progress", "depends_on": ["explore"],
             "on_failure": "explore", "max_retries": 2},
        ])
        _record(path, "design", "abandoned")
        raw = yaml.safe_load(open(path).read())
        by_id = {n["id"]: n for n in raw["workflow_plan"]["main"]["nodes"]}
        assert by_id["explore"]["status"] == "reset"
        assert by_id["design"]["status"] == "reset"
        assert raw.get("status") != "needs_you"
        assert raw["retries"]["design"] == 1

    def test_abandoned_on_failure_is_bounded_by_max_retries(self, tmp_path):
        path = _write_state(tmp_path, [
            {"id": "explore", "status": "completed", "depends_on": []},
            {"id": "design", "status": "in_progress", "depends_on": ["explore"],
             "on_failure": "explore", "max_retries": 1},
        ])
        _record(path, "design", "abandoned")
        _record(path, "design", "abandoned")
        raw = yaml.safe_load(open(path).read())
        assert raw["status"] == "blocked", "retry cap must escalate to blocked"


class TestStepReportsNeedsYou:
    """`step` must park the harness, not claim the run finished."""

    def test_dead_end_after_abandon_is_needs_you_not_done(self, tmp_path):
        from orchestrator_next import dispatch as dispatch_mod

        path = _write_state(tmp_path, [
            {"id": "design", "status": "in_progress", "depends_on": []},
            {"id": "design-review", "status": "pending", "depends_on": ["design"]},
        ])
        _record(path, "design", "abandoned")
        state = load_state(path)
        action, code = dispatch_mod.dispatch(state, path)
        assert code == dispatch_mod.EXIT_NEEDS_YOU, (
            f"expected needs_you exit, got {code} "
            "(exit 1 would tell the harness the run completed successfully)"
        )
        assert "design abandoned" in str((action or {}).get("detail") or "")

    def test_genuinely_complete_run_still_reports_done(self, tmp_path):
        from orchestrator_next import dispatch as dispatch_mod

        path = _write_state(tmp_path, [
            {"id": "design", "status": "completed", "depends_on": []},
        ])
        state = load_state(path)
        _, code = dispatch_mod.dispatch(state, path)
        assert code == 1
