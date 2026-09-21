"""
Tests for render_workflow_graph — static schema topology visualisation.
"""
import pytest

from orchestrator_next.graph import render_workflow_graph


@pytest.mark.parametrize("schema_name", ["feature", "bugfix", "complete", "implement", "patch"])
def test_render_workflow_graph_produces_mermaid(schema_name):
    src = render_workflow_graph(schema_name)
    assert src.startswith("flowchart TD\n")
    assert f"%% workflow: {schema_name}" in src


def test_render_workflow_graph_feature_has_steps():
    src = render_workflow_graph("feature")
    assert "check-rerun" in src
    assert "implement" in src
    assert "learn" in src


def test_render_workflow_graph_feature_has_retry_edge():
    src = render_workflow_graph("feature")
    assert "code_review -->|retry| implement" in src


def test_render_workflow_graph_linear_chain():
    src = render_workflow_graph("feature")
    assert "explore --> ux_design" in src


def test_render_workflow_graph_unknown_schema_raises():
    with pytest.raises(FileNotFoundError, match="nonexistent"):
        render_workflow_graph("nonexistent")
