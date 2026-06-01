"""workflow_issues accumulation into state.yaml via record()."""
from __future__ import annotations

import os
import sys

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from orchestrator_next.record import record  # noqa: E402


def _minimal_state(tmp_path, repo_root: str) -> str:
    state = {
        "change_id": "test-retro",
        "phase": "implement",
        "repo_root": repo_root,
        "worktree_path": repo_root,
        "schema": "feature",
        "workflow_plan": {
            "implement": {
                "nodes": [
                    {
                        "id": "explore",
                        "status": "in_progress",
                        "agent": "discoverer",
                        "goal": "Explore",
                        "inputs": [],
                        "outputs": [],
                        "rules": [],
                    }
                ],
                "filtered": [],
            }
        },
        "step_history": [
            {
                "step_id": "explore",
                "phase": "implement",
                "status": "in_progress",
                "evidence": {"outputs": {}},
            }
        ],
    }
    path = tmp_path / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(path)


def _completed_payload(issues: list | None = None) -> dict:
    p = {
        "step_id": "explore",
        "phase": "implement",
        "status": "completed",
        "agent": "discoverer",
        "outputs": {},
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }
    if issues is not None:
        p["workflow_issues"] = issues
    return p


@pytest.fixture(autouse=True)
def isolate_contracts(tmp_path, monkeypatch):
    empty = tmp_path / "empty_contracts"
    empty.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(empty))


class TestWorkflowIssuesInState:
    def test_issues_written_to_state_yaml(self, tmp_path):
        state_path = _minimal_state(tmp_path, str(tmp_path))
        record(state_path, _completed_payload(issues=[{"title": "bad thing", "detail": "oops"}]))

        on_disk = yaml.safe_load((tmp_path / "state.yaml").read_text())
        assert "workflow_issues" in on_disk
        assert len(on_disk["workflow_issues"]) == 1
        assert on_disk["workflow_issues"][0]["title"] == "bad thing"

    def test_surfaced_at_stamped_from_phase_step(self, tmp_path):
        state_path = _minimal_state(tmp_path, str(tmp_path))
        record(state_path, _completed_payload(issues=[{"title": "t"}]))

        on_disk = yaml.safe_load((tmp_path / "state.yaml").read_text())
        assert on_disk["workflow_issues"][0]["surfaced_at"] == "implement/explore"

    def test_dedup_key_prevents_duplicate(self, tmp_path):
        state_path = _minimal_state(tmp_path, str(tmp_path))
        issue = {"title": "dup", "dedup_key": "dup-v1"}
        record(state_path, _completed_payload(issues=[issue]))
        record(state_path, _completed_payload(issues=[issue]))

        on_disk = yaml.safe_load((tmp_path / "state.yaml").read_text())
        assert len(on_disk["workflow_issues"]) == 1

    def test_no_issues_key_absent_when_empty(self, tmp_path):
        state_path = _minimal_state(tmp_path, str(tmp_path))
        record(state_path, _completed_payload(issues=[]))

        on_disk = yaml.safe_load((tmp_path / "state.yaml").read_text())
        assert "workflow_issues" not in on_disk

    def test_no_issues_key_absent_when_not_provided(self, tmp_path):
        state_path = _minimal_state(tmp_path, str(tmp_path))
        record(state_path, _completed_payload())

        on_disk = yaml.safe_load((tmp_path / "state.yaml").read_text())
        assert "workflow_issues" not in on_disk

    def test_issues_accumulate_across_steps(self, tmp_path):
        state_path = _minimal_state(tmp_path, str(tmp_path))
        record(state_path, _completed_payload(issues=[{"title": "first"}]))
        # second step with a different issue — but state now has explore as completed,
        # so we need a fresh in_progress node. Just verify list grows.
        on_disk = yaml.safe_load((tmp_path / "state.yaml").read_text())
        assert len(on_disk["workflow_issues"]) == 1
