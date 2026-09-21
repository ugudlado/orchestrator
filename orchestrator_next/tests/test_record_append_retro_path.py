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
                "evidence": {"outputs": {"reason": "test"}},
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
        "outputs": {"reason": "test"},
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
