"""
ORC-63 T-5: RED tests for orchestrator_next.readiness — the shared DAG-walk
module (the single source of node readiness for dispatch.py and record.py).

Covers: effective_depends_on, is_node_ready, ready_nodes, next_ready_node,
mark_node_status, and the repeat_until interaction with readiness (OQ-5).
"""
from __future__ import annotations

import os
import sys

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


def _make_state(tmp_path, nodes, phase="main", extra=None):
    """Write a state.yaml with a `nodes`-shape workflow_plan and load it."""
    from orchestrator_next.parser import load_state
    data = {
        "change_id": "f",
        "phase": phase,
        "workflow_plan": {phase: {"nodes": nodes, "filtered": []}},
        "step_history": [],
    }
    if extra:
        data.update(extra)
    p = tmp_path / "state.yaml"
    p.write_text(yaml.dump(data))
    return load_state(str(p))


# ---------------------------------------------------------------------------
# effective_depends_on
# ---------------------------------------------------------------------------

def test_effective_depends_on_synthesizes_chain_edge():
    """A node with no depends_on inherits the implicit predecessor edge."""
    from orchestrator_next.readiness import effective_depends_on
    nodes = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    assert effective_depends_on(nodes, "b") == ["a"]
    assert effective_depends_on(nodes, "c") == ["b"]


def test_effective_depends_on_first_node_empty():
    """The first node of a phase has no implicit edge."""
    from orchestrator_next.readiness import effective_depends_on
    nodes = [{"id": "a"}, {"id": "b"}]
    assert effective_depends_on(nodes, "a") == []


def test_effective_depends_on_explicit_honored():
    """An authored depends_on is returned verbatim (no implicit edge)."""
    from orchestrator_next.readiness import effective_depends_on
    nodes = [{"id": "a"}, {"id": "b"}, {"id": "c", "depends_on": ["a"]}]
    assert effective_depends_on(nodes, "c") == ["a"]


# ---------------------------------------------------------------------------
# is_node_ready
# ---------------------------------------------------------------------------

def test_is_node_ready_true_only_when_deps_completed(tmp_path):
    """A node is ready only when every effective dependency is completed."""
    from orchestrator_next.readiness import is_node_ready
    state = _make_state(tmp_path, [
        {"id": "a", "status": "completed"},
        {"id": "b", "status": "pending"},
        {"id": "c", "status": "pending"},
    ])
    assert is_node_ready(state, "b") is True   # dep a completed
    assert is_node_ready(state, "c") is False  # dep b not completed


def test_is_node_ready_false_for_completed_node(tmp_path):
    """A completed node is not itself 'ready' (nothing to dispatch)."""
    from orchestrator_next.readiness import is_node_ready
    state = _make_state(tmp_path, [
        {"id": "a", "status": "completed"},
        {"id": "b", "status": "pending"},
    ])
    assert is_node_ready(state, "a") is False


def test_repeat_until_unknown_predicate_does_not_block(tmp_path):
    """ORC-65 T-9: all_tasks_completed removed from REPEAT_PREDICATES.
    An unknown predicate is treated as satisfied (returns True), so a
    completed node with an unknown repeat_until does NOT block dependents.
    """
    from orchestrator_next.readiness import is_node_ready
    state = _make_state(tmp_path, [
        {"id": "exec", "status": "completed", "repeat_until": "all_tasks_completed"},
        {"id": "review", "status": "pending"},
    ], extra={"repo_root": str(tmp_path)})
    # all_tasks_completed not in REPEAT_PREDICATES -> unknown -> treated as True
    # -> review IS ready (dep satisfied)
    assert is_node_ready(state, "review") is True


def test_repeat_until_dependency_ready_when_no_predicate(tmp_path):
    """A node with repeat_until and status=completed (no blocking predicate)
    allows its dependent to become ready."""
    from orchestrator_next.readiness import is_node_ready
    state = _make_state(tmp_path, [
        {"id": "exec", "status": "completed", "repeat_until": "all_tasks_completed"},
        {"id": "review", "status": "pending"},
    ], extra={"repo_root": str(tmp_path)})
    assert is_node_ready(state, "review") is True


# ---------------------------------------------------------------------------
# ready_nodes / next_ready_node
# ---------------------------------------------------------------------------

def test_ready_nodes_declaration_order(tmp_path):
    """ready_nodes returns ready, not-completed nodes in declaration order."""
    from orchestrator_next.readiness import ready_nodes
    state = _make_state(tmp_path, [
        {"id": "a", "status": "completed"},
        {"id": "b", "status": "pending"},
        {"id": "c", "status": "pending"},
    ])
    # only b is ready (c depends on b)
    assert ready_nodes(state) == ["b"]


def test_next_ready_node_first_or_none(tmp_path):
    """next_ready_node returns ready_nodes[0], or None when phase complete."""
    from orchestrator_next.readiness import next_ready_node
    state = _make_state(tmp_path, [
        {"id": "a", "status": "completed"},
        {"id": "b", "status": "pending"},
    ])
    assert next_ready_node(state) == "b"
    done = _make_state(tmp_path, [
        {"id": "a", "status": "completed"},
        {"id": "b", "status": "completed"},
    ])
    assert next_ready_node(done) is None


# ---------------------------------------------------------------------------
# mark_node_status
# ---------------------------------------------------------------------------

def test_mark_node_status_flips_one_node(tmp_path):
    """mark_node_status flips exactly the named node's status in workflow_plan."""
    from orchestrator_next.readiness import mark_node_status
    state_raw = {
        "phase": "main",
        "workflow_plan": {"main": {"nodes": [
            {"id": "a", "status": "pending"},
            {"id": "b", "status": "pending"},
        ]}},
    }
    mark_node_status(state_raw, "main", "a", "in_progress")
    nodes = state_raw["workflow_plan"]["main"]["nodes"]
    assert nodes[0]["status"] == "in_progress"
    assert nodes[1]["status"] == "pending"


def test_readiness_module_imports():
    """The readiness module is importable (fails today — ModuleNotFoundError)."""
    import orchestrator_next.readiness  # noqa: F401
