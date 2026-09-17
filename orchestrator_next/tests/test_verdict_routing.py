"""Structured verdict routing: `out.verdict: needs_work` must not advance.

Live run 01a0af00: design-review recorded `--status completed` with
`out.verdict: needs_work` and every axis 1/5. The engine keys routing on the
payload status alone, so the node advanced and the signoff gate minted a
token over a design nobody had approved.

A judgment contract declaring an enum out with `fail_on:` now routes through
`on_failure` when the reported value is in that list, exactly as an
`abandoned` status does — same edge, same max_retries cap.
"""
from __future__ import annotations

import pytest
import yaml

from orchestrator_next import readiness
from orchestrator_next.parser import load_state
from orchestrator_next.record import record


CONTRACT = """
id: review
version: 2
kind: judgment
prompt: SKILL.md
out:
  verdict: {type: enum, values: [pass, needs_work], fail_on: [needs_work]}
"""


@pytest.fixture
def contracts(tmp_path, monkeypatch):
    root = tmp_path / "contracts"
    (root / "review").mkdir(parents=True)
    (root / "review" / "contract.yaml").write_text(CONTRACT)
    (root / "review" / "SKILL.md").write_text("# review\n")
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(root))
    return root


def _write_state(tmp_path, nodes):
    state = {
        "change_id": "verdict-test",
        "phase": "main",
        "schema": "feature",
        "workflow_plan": {"main": {"nodes": nodes, "filtered": []}},
        "step_history": [],
    }
    path = tmp_path / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(path)


def _record(path, verdict):
    return record(path, {
        "step_id": "review",
        "phase": "main",
        "status": "completed",
        "agent": "standard",
        "outputs": {"verdict": verdict, "reason": "axes 1/5"},
        "usage": {"input_tokens": 10, "output_tokens": 5},
    })


def _nodes():
    return [
        {"id": "make", "status": "completed", "depends_on": []},
        {"id": "review", "status": "in_progress", "depends_on": ["make"],
         "on_failure": "make", "max_retries": 2},
        {"id": "ship", "status": "pending", "depends_on": ["review"]},
    ]


class TestFailingVerdictRoutes:
    def test_needs_work_routes_through_on_failure(self, tmp_path, contracts):
        path = _write_state(tmp_path, _nodes())
        _record(path, "needs_work")
        raw = yaml.safe_load(open(path).read())
        by_id = {n["id"]: n for n in raw["workflow_plan"]["main"]["nodes"]}
        assert by_id["make"]["status"] == "reset"
        assert by_id["review"]["status"] == "reset"

    def test_needs_work_does_not_make_dependents_ready(self, tmp_path, contracts):
        path = _write_state(tmp_path, _nodes())
        _record(path, "needs_work")
        state = load_state(path)
        assert not readiness.is_node_ready(state, "ship"), (
            "a needs_work review must not let the next step (or a signoff "
            "gate) proceed"
        )
        assert readiness.next_ready_node(state) == "make"

    def test_needs_work_is_bounded_by_max_retries(self, tmp_path, contracts):
        path = _write_state(tmp_path, _nodes())
        _record(path, "needs_work")
        _record(path, "needs_work")
        _record(path, "needs_work")
        raw = yaml.safe_load(open(path).read())
        assert raw["status"] == "blocked"

    def test_passing_verdict_still_advances(self, tmp_path, contracts):
        path = _write_state(tmp_path, _nodes())
        _record(path, "pass")
        raw = yaml.safe_load(open(path).read())
        by_id = {n["id"]: n for n in raw["workflow_plan"]["main"]["nodes"]}
        assert by_id["review"]["status"] == "completed"
        state = load_state(path)
        assert readiness.is_node_ready(state, "ship")

    def test_enum_without_fail_on_always_advances(self, tmp_path, monkeypatch):
        """`fail_on:` is opt-in — a plain enum out keeps today's behaviour."""
        root = tmp_path / "c2"
        (root / "review").mkdir(parents=True)
        (root / "review" / "contract.yaml").write_text(CONTRACT.replace(
            ", fail_on: [needs_work]", ""))
        (root / "review" / "SKILL.md").write_text("# review\n")
        monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(root))
        path = _write_state(tmp_path, _nodes())
        _record(path, "needs_work")
        raw = yaml.safe_load(open(path).read())
        by_id = {n["id"]: n for n in raw["workflow_plan"]["main"]["nodes"]}
        assert by_id["review"]["status"] == "completed"


class TestReworkIsNotSkippedAsUnchanged:
    """A node reset for rework must re-run, not be skipped as idempotent.

    `_skip_unchanged` exists so a *resume* does not redo work whose inputs and
    outputs still hash the same. A node the router reset after a needs_work
    verdict looks exactly like that — same files, recorded artifacts — so the
    skip marked it completed and the run walked straight on to the signoff
    gate, skipping the re-review the rework loop exists to perform.
    """

    def test_a_reset_node_is_not_skipped_as_unchanged(self, tmp_path, contracts,
                                                      monkeypatch):
        from orchestrator_next import dispatch as dispatch_mod

        path = _write_state(tmp_path, [
            {"id": "make", "status": "completed", "depends_on": []},
            {"id": "review", "status": "reset", "depends_on": ["make"],
             "on_failure": "make", "max_retries": 2,
             "artifacts": [{"name": "design", "path": "design.md",
                            "sha256": "abc"}]},
            {"id": "ship", "status": "pending", "depends_on": ["review"]},
        ])
        # The point of the test is the reset, not the hashing: force the
        # idempotency check to say "identical" so only the reset can save us.
        monkeypatch.setattr(
            "orchestrator_next.artifacts.node_is_unchanged",
            lambda *a, **k: True,
        )
        state = load_state(path)
        remaining, _ = dispatch_mod._skip_unchanged(
            state, ["review"], path,
        )
        assert remaining == ["review"], (
            "a node reset for rework must still be dispatched"
        )
        raw = yaml.safe_load(open(path).read())
        by_id = {n["id"]: n for n in raw["workflow_plan"]["main"]["nodes"]}
        assert by_id["review"]["status"] != "completed"
