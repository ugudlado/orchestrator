"""
Tests for graph cost/token/attempt overlay (ORC-122).
"""
from __future__ import annotations

import os
import sys

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT_STR = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _REPO_ROOT_STR not in sys.path:
    sys.path.insert(0, _REPO_ROOT_STR)

from orchestrator_next.graph import (  # noqa: E402
    _aggregate_step_metrics,
    render_workflow_graph,
    render_workflow_graph_with_overlay,
)

# Golden output captured from render_workflow_graph('feature') at T-1 time.
_FEATURE_GRAPH_GOLDEN = (
    "flowchart TD\n"
    "  %% workflow: feature\n"
    '  check_rerun["check-rerun"]\n'
    '  create_worktree["create-worktree"]\n'
    '  load_ticket_context["load-ticket-context"]\n'
    '  explore["explore"]\n'
    '  ux_design["ux-design"]\n'
    '  design_and_draft_artifacts["design-and-draft-artifacts"]\n'
    '  design_review["design-review"]\n'
    '  ticket_start["ticket-start"]\n'
    '  run_ux_critique["run-ux-critique"]\n'
    '  implement_tasks["implement-tasks"]\n'
    '  ticket_review["ticket-review"]\n'
    '  run_phase_review["run-phase-review"]\n'
    '  ticket_qa["ticket-qa"]\n'
    '  run_learn_cycle["run-learn-cycle"]\n'
    "  check_rerun --> create_worktree\n"
    "  create_worktree --> load_ticket_context\n"
    "  load_ticket_context --> explore\n"
    "  explore --> ux_design\n"
    "  ux_design --> design_and_draft_artifacts\n"
    "  design_and_draft_artifacts --> design_review\n"
    "  design_review -->|retry| design_and_draft_artifacts\n"
    "  design_review --> ticket_start\n"
    "  ticket_start --> run_ux_critique\n"
    "  run_ux_critique --> implement_tasks\n"
    "  implement_tasks -->|retry| design_and_draft_artifacts\n"
    "  implement_tasks --> ticket_review\n"
    "  ticket_review --> run_phase_review\n"
    "  run_phase_review --> ticket_qa\n"
    "  run_phase_review -->|retry| implement_tasks\n"
    "  ticket_qa --> run_learn_cycle\n"
)


def _write_state(path, step_history, *, change_id="demo", schema="feature"):
    data = {
        "change_id": change_id,
        "schema": schema,
        "step_history": step_history,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data), encoding="utf-8")


def _agent_entry(step_id, *, attempt=1, input_tokens=1000, output_tokens=500, cost_usd=1.05):
    return {
        "step_id": step_id,
        "attempt": attempt,
        "status": "completed",
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost_usd,
        },
    }


def _script_entry(step_id, *, attempt=1):
    return {
        "step_id": step_id,
        "attempt": attempt,
        "status": "completed",
        "usage": {},
    }


def test_aggregate_step_metrics_globs_and_merges(tmp_path):
    state_dir = tmp_path / "states"
    _write_state(
        state_dir / "20260101T000000_feature_state.yaml",
        [_agent_entry("implement-tasks", input_tokens=8000, output_tokens=1278, cost_usd=1.05)],
    )
    _write_state(
        state_dir / "20260102T000000_complete_state.yaml",
        [_script_entry("run-learn-cycle")],
        schema="complete",
    )

    metrics = _aggregate_step_metrics(state_dir)

    assert "implement-tasks" in metrics
    assert "run-learn-cycle" in metrics
    assert metrics["implement-tasks"]["tokens"] == 9278
    assert metrics["implement-tasks"]["cost"] == pytest.approx(1.05)
    assert metrics["run-learn-cycle"]["tokens"] == 0


def test_aggregate_step_metrics_sums_and_max_attempt(tmp_path):
    state_dir = tmp_path / "states"
    _write_state(
        state_dir / "20260101T000000_feature_state.yaml",
        [
            _agent_entry("implement-tasks", attempt=1, input_tokens=50000, output_tokens=10000, cost_usd=2.50),
            _agent_entry("implement-tasks", attempt=2, input_tokens=30000, output_tokens=6440, cost_usd=2.91),
        ],
    )

    metrics = _aggregate_step_metrics(state_dir)

    assert metrics["implement-tasks"]["tokens"] == 96440
    assert metrics["implement-tasks"]["cost"] == pytest.approx(5.41)
    assert metrics["implement-tasks"]["attempts"] == 2


def test_overlay_annotates_agent_step_labels(tmp_path):
    state_dir = tmp_path / "states"
    _write_state(
        state_dir / "20260101T000000_feature_state.yaml",
        [_agent_entry("implement-tasks", input_tokens=8000, output_tokens=1278, cost_usd=1.05)],
    )

    src, _ = render_workflow_graph_with_overlay("feature", state_dir)

    assert "implement_tasks" in src
    assert "tok ·" in src
    assert "$1.05" in src
    assert "9,278 tok · $1.05" in src


def test_overlay_script_only_steps_plain(tmp_path):
    state_dir = tmp_path / "states"
    _write_state(
        state_dir / "20260101T000000_feature_state.yaml",
        [_script_entry("check-rerun")],
    )

    src, _ = render_workflow_graph_with_overlay("feature", state_dir)

    assert 'check_rerun["check-rerun"]' in src
    assert "tok ·" not in src.split('check_rerun["check-rerun"]')[1].split("\n")[0]


def test_overlay_retry_style_for_multiple_attempts(tmp_path):
    state_dir = tmp_path / "states"
    _write_state(
        state_dir / "20260101T000000_feature_state.yaml",
        [
            _agent_entry("run-phase-review", attempt=1, input_tokens=1000, output_tokens=200, cost_usd=0.10),
            _agent_entry("run-phase-review", attempt=2, input_tokens=2000, output_tokens=400, cost_usd=0.20),
            _agent_entry("explore", attempt=1, input_tokens=5000, output_tokens=1000, cost_usd=0.50),
        ],
    )

    src, _ = render_workflow_graph_with_overlay("feature", state_dir)

    assert "style run_phase_review fill:#f90" in src
    assert "style explore fill:#f90" not in src


def test_overlay_returns_step_data_for_sidebar(tmp_path):
    state_dir = tmp_path / "states"
    entry = _agent_entry("implement-tasks", input_tokens=8000, output_tokens=1278, cost_usd=1.05)
    _write_state(state_dir / "20260101T000000_feature_state.yaml", [entry])

    _, step_data = render_workflow_graph_with_overlay("feature", state_dir)

    assert "implement-tasks" in step_data
    assert step_data["implement-tasks"]["usage"]["input_tokens"] == 8000
    assert step_data["implement-tasks"]["usage"]["cost_usd"] == pytest.approx(1.05)


def test_overlay_emits_click_callbacks(tmp_path):
    state_dir = tmp_path / "states"
    _write_state(
        state_dir / "20260101T000000_feature_state.yaml",
        [_agent_entry("implement-tasks")],
    )

    src, _ = render_workflow_graph_with_overlay("feature", state_dir)

    assert "click implement_tasks showStep" in src


def test_render_workflow_graph_unchanged_no_overlay():
    src, step_data = render_workflow_graph("feature")

    assert step_data == {}
    assert src == _FEATURE_GRAPH_GOLDEN
    assert "click " not in src
    assert "style " not in src
