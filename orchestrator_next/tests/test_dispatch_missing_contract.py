"""
FT-20 regression test for dispatch.py.

When workflow_plan is frozen at pre-dispatch init time and a step contract
referenced in the plan is later deleted (e.g. Stage B of cleanup-and-delete
removed `ingest-feature-metrics.yaml` while the in-flight workflow's plan
still listed it), the dispatcher must NOT raise FileNotFoundError. It must
fall back to a minimal inline contract so the workflow can advance past
the orphaned step.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from orchestrator_next.dispatch import dispatch  # noqa: E402
from orchestrator_next.parser import ContractNotFoundError as ContractDispatchError, load_state  # noqa: E402


def _write_state(tmp_path: Path, *, deleted_step: str) -> Path:
    state = {
        "schema": "feature",
        "change_id": "ft20-test",
        "slug": "ft20-test",
        "status": "active",
        "repo_root": str(tmp_path),
        "phase": "complete",
        "next_step": {"phase": "complete", "step_id": deleted_step},
        "workflow_plan": {
            "complete": {
                "nodes": [
                    {"id": deleted_step, "status": "pending", "agent": "developer",
                     "goal": "deleted", "inputs": [], "outputs": [], "rules": []},
                    {"id": "remove-worktree", "status": "pending", "agent": "developer",
                     "goal": "ok", "inputs": [], "outputs": [], "rules": []},
                ],
                "filtered": [],
            },
        },
        "step_history": [],
    }
    state_path = tmp_path / "state.yaml"
    state_path.write_text(yaml.safe_dump(state))

    # Also drop a minimal plan.yaml so dispatch's _load_plan succeeds.
    plan = {
        "phases": [
            {
                "name": "complete",
                "steps": [
                    {"id": deleted_step, "goal": "deleted"},
                    {"id": "remove-worktree", "goal": "ok"},
                ],
            }
        ]
    }
    (tmp_path / "plan.yaml").write_text(yaml.safe_dump(plan))
    return state_path


def test_dispatch_raises_when_contract_missing_and_stub_has_no_run(tmp_path):
    """Orphan step: deleted contract falls back to agent=None, run=None stub → dispatch error."""
    state_path = _write_state(tmp_path, deleted_step="step-deleted-from-disk")
    state = load_state(str(state_path))
    with pytest.raises(ContractDispatchError, match="step_contract_missing_run"):
        dispatch(state, str(state_path))
