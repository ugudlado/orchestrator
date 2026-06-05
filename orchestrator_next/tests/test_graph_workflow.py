"""
Tests for render_workflow_graph — static schema topology visualisation.
"""
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT_STR = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _REPO_ROOT_STR not in sys.path:
    sys.path.insert(0, _REPO_ROOT_STR)

from orchestrator_next.graph import render_workflow_graph


@pytest.mark.parametrize("schema_name", ["feature", "bugfix", "autopilot", "complete", "implement", "patch"])
def test_render_workflow_graph_produces_mermaid(schema_name):
    src, step_data = render_workflow_graph(schema_name)
    assert src.startswith("flowchart TD\n")
    assert f"%% workflow: {schema_name}" in src
    assert step_data == {}


def test_render_workflow_graph_feature_has_steps():
    src, _ = render_workflow_graph("feature")
    assert "check-rerun" in src
    assert "implement-tasks" in src
    assert "run-learn-cycle" in src


def test_render_workflow_graph_feature_has_retry_edge():
    src, _ = render_workflow_graph("feature")
    assert "run_phase_review -->|retry| implement_tasks" in src


def test_render_workflow_graph_linear_chain():
    src, _ = render_workflow_graph("feature")
    assert "explore --> ux_design" in src


def test_render_workflow_graph_unknown_schema_raises():
    with pytest.raises(FileNotFoundError, match="nonexistent"):
        render_workflow_graph("nonexistent")
