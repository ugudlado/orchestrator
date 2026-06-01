"""Tests for orchestrator reset-step — resets a node and all subsequent nodes to pending."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from orchestrator_next.reset_step import reset_step  # noqa: E402


def _write_state(tmp_path: Path, nodes: list[dict], history: list[dict] | None = None) -> Path:
    state = {
        "change_id": "test-feature",
        "phase": "implement",
        "workflow_plan": {
            "implement": {
                "nodes": nodes,
            }
        },
        "step_history": history or [],
    }
    p = tmp_path / "state.yaml"
    p.write_text(yaml.safe_dump(state, sort_keys=False))
    return p


def _load(p: Path) -> dict:
    return yaml.safe_load(p.read_text()) or {}


def test_resets_target_and_subsequent_nodes(tmp_path):
    p = _write_state(tmp_path, [
        {"id": "design-and-draft-artifacts", "status": "completed"},
        {"id": "design-review", "status": "completed"},
        {"id": "implement-tasks", "status": "in_progress"},
    ])
    reset_step("design-and-draft-artifacts", str(p))
    state = _load(p)
    nodes = {n["id"]: n["status"] for n in state["workflow_plan"]["implement"]["nodes"]}
    assert nodes["design-and-draft-artifacts"] == "pending"
    assert nodes["design-review"] == "pending"
    assert nodes["implement-tasks"] == "pending"


def test_does_not_reset_nodes_before_target(tmp_path):
    p = _write_state(tmp_path, [
        {"id": "explore", "status": "completed"},
        {"id": "design-and-draft-artifacts", "status": "completed"},
        {"id": "design-review", "status": "completed"},
    ])
    reset_step("design-and-draft-artifacts", str(p))
    state = _load(p)
    nodes = {n["id"]: n["status"] for n in state["workflow_plan"]["implement"]["nodes"]}
    assert nodes["explore"] == "completed"
    assert nodes["design-and-draft-artifacts"] == "pending"
    assert nodes["design-review"] == "pending"


def test_strips_step_history_for_reset_nodes(tmp_path):
    history = [
        {"step_id": "explore", "phase": "implement", "status": "completed"},
        {"step_id": "design-and-draft-artifacts", "phase": "implement", "status": "completed"},
        {"step_id": "design-review", "phase": "implement", "status": "completed"},
    ]
    p = _write_state(tmp_path, [
        {"id": "explore", "status": "completed"},
        {"id": "design-and-draft-artifacts", "status": "completed"},
        {"id": "design-review", "status": "completed"},
    ], history=history)
    reset_step("design-and-draft-artifacts", str(p))
    state = _load(p)
    remaining_ids = [e["step_id"] for e in state["step_history"]]
    assert "explore" in remaining_ids
    assert "design-and-draft-artifacts" not in remaining_ids
    assert "design-review" not in remaining_ids


def test_clears_next_step_when_pointing_at_reset_node(tmp_path):
    p = _write_state(tmp_path, [
        {"id": "design-and-draft-artifacts", "status": "completed"},
        {"id": "design-review", "status": "completed"},
    ])
    state = yaml.safe_load(p.read_text()) or {}
    state["next_step"] = {"step_id": "design-review", "phase": "implement"}
    p.write_text(yaml.safe_dump(state))

    reset_step("design-and-draft-artifacts", str(p))
    result = _load(p)
    assert "next_step" not in result


def test_unknown_step_id_raises(tmp_path):
    p = _write_state(tmp_path, [
        {"id": "design-and-draft-artifacts", "status": "completed"},
    ])
    with pytest.raises(ValueError, match="not found"):
        reset_step("nonexistent-step", str(p))


def test_idempotent_on_already_pending(tmp_path):
    p = _write_state(tmp_path, [
        {"id": "design-and-draft-artifacts", "status": "pending"},
        {"id": "design-review", "status": "pending"},
    ])
    reset_step("design-and-draft-artifacts", str(p))
    state = _load(p)
    nodes = {n["id"]: n["status"] for n in state["workflow_plan"]["implement"]["nodes"]}
    assert nodes["design-and-draft-artifacts"] == "pending"
    assert nodes["design-review"] == "pending"
