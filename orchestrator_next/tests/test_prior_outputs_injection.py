"""Tests for ORCHESTRATOR_PRIOR_OUTPUTS injection in build_dispatch_env."""
from __future__ import annotations

import json

from orchestrator_next.parser import State, StepHistoryEntry
from orchestrator_next.step_env import build_dispatch_env


def _entry(step_id: str, status: str, outputs: dict) -> StepHistoryEntry:
    raw = {
        "step_id": step_id,
        "phase": "main",
        "status": status,
        "evidence": {"outputs": outputs},
    }
    return StepHistoryEntry(
        step_id=step_id,
        phase="main",
        status=status,
        agent=None,
        attempt=1,
        started_at=None,
        ended_at="2026-06-06T00:00:00Z",
        usage={},
        escalation=None,
        raw=raw,
    )


def _state(nodes: list[dict], history: list[StepHistoryEntry]) -> State:
    return State(
        change_id="test",
        phase="main",
        repo_root="/repo",
        workflow_dir="/repo",
        worktree_artifact_dir="/repo/spec/changes",
        workflow_plan={"main": {"nodes": nodes, "filtered": []}},
        step_history=history,
        raw={},
    )


class TestPriorOutputsInjection:

    def test_injects_declared_inputs_from_history(self):
        history = [_entry("explore", "completed", {"discovery_result": {"findings": ["x"]}})]
        state = _state(
            nodes=[
                {"id": "explore", "status": "completed", "inputs": [], "outputs": ["discovery_result"]},
                {"id": "design", "status": "pending", "inputs": ["discovery_result"], "outputs": []},
            ],
            history=history,
        )
        env = build_dispatch_env(state, "design", attempt=1)
        assert "ORCHESTRATOR_PRIOR_OUTPUTS" in env
        prior = json.loads(env["ORCHESTRATOR_PRIOR_OUTPUTS"])
        assert prior["discovery_result"] == {"findings": ["x"]}

    def test_no_injection_when_inputs_empty(self):
        history = [_entry("explore", "completed", {"discovery_result": {}})]
        state = _state(
            nodes=[{"id": "explore", "status": "completed", "inputs": [], "outputs": []}],
            history=history,
        )
        env = build_dispatch_env(state, "explore", attempt=1)
        assert "ORCHESTRATOR_PRIOR_OUTPUTS" not in env

    def test_uses_most_recent_completed_entry(self):
        """On rework loops, the latest completed value wins."""
        history = [
            _entry("explore", "completed", {"discovery_result": {"findings": ["old"]}}),
            _entry("explore", "completed", {"discovery_result": {"findings": ["new"]}}),
        ]
        state = _state(
            nodes=[
                {"id": "explore", "status": "completed", "inputs": [], "outputs": ["discovery_result"]},
                {"id": "design", "status": "pending", "inputs": ["discovery_result"], "outputs": []},
            ],
            history=history,
        )
        env = build_dispatch_env(state, "design", attempt=1)
        prior = json.loads(env["ORCHESTRATOR_PRIOR_OUTPUTS"])
        assert prior["discovery_result"] == {"findings": ["new"]}

    def test_skips_non_completed_entries(self):
        """abandoned/failed entries do not contribute to prior outputs."""
        history = [
            _entry("explore", "failed", {"discovery_result": {"findings": ["bad"]}}),
        ]
        state = _state(
            nodes=[
                {"id": "explore", "status": "pending", "inputs": [], "outputs": ["discovery_result"]},
                {"id": "design", "status": "pending", "inputs": ["discovery_result"], "outputs": []},
            ],
            history=history,
        )
        env = build_dispatch_env(state, "design", attempt=1)
        assert "ORCHESTRATOR_PRIOR_OUTPUTS" not in env

    def test_no_injection_when_step_not_in_plan(self):
        """Step absent from plan nodes → no injection, no error."""
        state = _state(nodes=[], history=[])
        env = build_dispatch_env(state, "ghost-step", attempt=1)
        assert "ORCHESTRATOR_PRIOR_OUTPUTS" not in env
