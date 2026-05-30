"""T-3 tests: orchestrator_next.expand_plan module.

RED phase: tests fail before expand_plan.py is created.
"""
from __future__ import annotations

import hashlib
import os
import sys

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

MINIMAL_TASKS_YAML = {
    "version": 1,
    "tasks": [
        {
            "id": "T-1",
            "title": "Wire X to Y",
            "files": ["a.py"],
            "verify": ["pytest a.py"],
            "depends_on": [],
        },
        {
            "id": "T-2",
            "title": "Add test",
            "files": ["test_a.py"],
            "verify": ["pytest test_a.py"],
            "depends_on": ["T-1"],
        },
        {
            "id": "T-3",
            "title": "Document",
            "files": ["README.md"],
            "verify": ["echo ok"],
            "depends_on": ["T-2"],
        },
    ],
}


def _make_state(tmp_path, tasks_yaml_content=None) -> tuple[str, str]:
    """Write tasks.yaml and a minimal state.yaml; return (state_yaml_path, tasks_yaml_path)."""
    spec_changes = tmp_path / "spec" / "changes" / "test-feature"
    spec_changes.mkdir(parents=True)

    tasks_yaml_path = spec_changes / "tasks.yaml"
    content = tasks_yaml_content if tasks_yaml_content is not None else MINIMAL_TASKS_YAML
    tasks_yaml_path.write_text(
        yaml.safe_dump(content, sort_keys=False, default_flow_style=False)
    )

    state = {
        "change_id": "test-feature",
        "phase": "implement",
        "schema": "feature",
        "repo_root": str(tmp_path),
        "worktree_path": str(tmp_path),
        "workflow_plan": {
            "implement": {
                "nodes": [
                    {
                        "id": "design-and-draft-artifacts",
                        "status": "completed",
                        "agent": "architect",
                        "goal": "Generate design",
                        "inputs": [],
                        "outputs": ["design.md"],
                        "rules": [],
                    },
                    {
                        "id": "expand-plan",
                        "status": "completed",
                        "agent": None,
                        "goal": "Expand plan",
                        "inputs": [],
                        "outputs": [],
                        "rules": [],
                    },
                    {
                        "id": "run-phase-review",
                        "status": "pending",
                        "agent": "reviewer",
                        "goal": "Review",
                        "inputs": [],
                        "outputs": ["phase_review_report"],
                        "rules": [],
                        "depends_on": ["expand-plan"],
                    },
                ],
                "filtered": [],
            }
        },
        "step_history": [],
    }

    state_path = tmp_path / "state.yaml"
    state_path.write_text(yaml.safe_dump(state, sort_keys=False, default_flow_style=False))
    return str(state_path), str(tasks_yaml_path)


def _load_state(state_path: str) -> dict:
    with open(state_path) as f:
        return yaml.safe_load(f)


def _sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------

from orchestrator_next import expand_plan  # noqa: E402


# ---------------------------------------------------------------------------
# Core behavior
# ---------------------------------------------------------------------------

class TestExpandPlanFirstInvocation:

    def test_appends_n_task_nodes_for_n_tasks(self, tmp_path):
        """First invocation appends one task-node per task in tasks.yaml."""
        state_path, _ = _make_state(tmp_path)
        expand_plan.expand_plan(state_path)
        raw = _load_state(state_path)
        nodes = raw["workflow_plan"]["implement"]["nodes"]
        node_ids = [n["id"] for n in nodes]
        assert "task-T-1" in node_ids
        assert "task-T-2" in node_ids
        assert "task-T-3" in node_ids

    def test_task_nodes_have_correct_agent(self, tmp_path):
        """Task-nodes get agent: developer."""
        state_path, _ = _make_state(tmp_path)
        expand_plan.expand_plan(state_path)
        raw = _load_state(state_path)
        nodes = {n["id"]: n for n in raw["workflow_plan"]["implement"]["nodes"]}
        assert nodes["task-T-1"]["agent"] == "developer"
        assert nodes["task-T-2"]["agent"] == "developer"

    def test_task_nodes_have_correct_step_contract(self, tmp_path):
        """Task-nodes get step_contract: execute-one-task."""
        state_path, _ = _make_state(tmp_path)
        expand_plan.expand_plan(state_path)
        raw = _load_state(state_path)
        nodes = {n["id"]: n for n in raw["workflow_plan"]["implement"]["nodes"]}
        assert nodes["task-T-1"]["step_contract"] == "execute-one-task"

    def test_task_nodes_have_correct_depends_on(self, tmp_path):
        """depends_on is mapped through the task- prefix."""
        state_path, _ = _make_state(tmp_path)
        expand_plan.expand_plan(state_path)
        raw = _load_state(state_path)
        nodes = {n["id"]: n for n in raw["workflow_plan"]["implement"]["nodes"]}
        # T-1 has no deps → no depends_on key (or empty)
        t1_deps = nodes["task-T-1"].get("depends_on", [])
        assert t1_deps == []
        # T-2 depends on T-1
        assert nodes["task-T-2"]["depends_on"] == ["task-T-1"]
        # T-3 depends on T-2
        assert nodes["task-T-3"]["depends_on"] == ["task-T-2"]

    def test_task_nodes_have_task_payload(self, tmp_path):
        """Each task-node carries the full task as a 'task' key."""
        state_path, _ = _make_state(tmp_path)
        expand_plan.expand_plan(state_path)
        raw = _load_state(state_path)
        nodes = {n["id"]: n for n in raw["workflow_plan"]["implement"]["nodes"]}
        task_payload = nodes["task-T-1"]["task"]
        assert task_payload["id"] == "T-1"
        assert task_payload["title"] == "Wire X to Y"
        assert "a.py" in task_payload["files"]
        assert "pytest a.py" in task_payload["verify"]

    def test_task_nodes_have_pending_status(self, tmp_path):
        """Newly appended task-nodes are status: pending."""
        state_path, _ = _make_state(tmp_path)
        expand_plan.expand_plan(state_path)
        raw = _load_state(state_path)
        nodes = {n["id"]: n for n in raw["workflow_plan"]["implement"]["nodes"]}
        assert nodes["task-T-1"]["status"] == "pending"

    def test_run_phase_review_rewired(self, tmp_path):
        """run-phase-review.depends_on becomes [last task-node id]."""
        state_path, _ = _make_state(tmp_path)
        expand_plan.expand_plan(state_path)
        raw = _load_state(state_path)
        nodes = {n["id"]: n for n in raw["workflow_plan"]["implement"]["nodes"]}
        rpr = nodes["run-phase-review"]
        assert rpr["depends_on"] == ["task-T-3"]


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

class TestExpandPlanIdempotency:

    def test_second_invocation_appends_nothing(self, tmp_path):
        """Second invocation is a no-op — state.yaml byte-identical."""
        state_path, _ = _make_state(tmp_path)
        expand_plan.expand_plan(state_path)
        sha1 = _sha256(state_path)
        expand_plan.expand_plan(state_path)
        sha2 = _sha256(state_path)
        assert sha1 == sha2, "Second expand-plan invocation mutated state.yaml"

    def test_partial_completion_does_not_duplicate(self, tmp_path):
        """If task-T-1 is already present (and completed), expand-plan adds T-2/T-3 only."""
        state_path, _ = _make_state(tmp_path)
        # Add task-T-1 manually as already-completed
        raw = _load_state(state_path)
        nodes = raw["workflow_plan"]["implement"]["nodes"]
        nodes.insert(2, {
            "id": "task-T-1",
            "status": "completed",
            "agent": "developer",
            "step_contract": "execute-one-task",
            "goal": "Wire X to Y",
            "inputs": [],
            "outputs": ["task_execution_result"],
            "rules": [],
            "task": {"id": "T-1", "title": "Wire X to Y", "files": ["a.py"], "verify": ["pytest a.py"]},
        })
        with open(state_path, "w") as f:
            yaml.safe_dump(raw, f, sort_keys=False, default_flow_style=False)

        expand_plan.expand_plan(state_path)
        raw2 = _load_state(state_path)
        all_ids = [n["id"] for n in raw2["workflow_plan"]["implement"]["nodes"]]
        # task-T-1 should appear exactly once
        assert all_ids.count("task-T-1") == 1
        # task-T-2 and task-T-3 should be present
        assert "task-T-2" in all_ids
        assert "task-T-3" in all_ids


# ---------------------------------------------------------------------------
# Error cases — state.yaml unchanged on disk
# ---------------------------------------------------------------------------

class TestExpandPlanErrors:

    def test_cycle_raises_and_state_unchanged(self, tmp_path):
        """Cycle in tasks.yaml raises; state.yaml unchanged."""
        cycle_tasks = {
            "version": 1,
            "tasks": [
                {"id": "T-1", "title": "A", "files": ["a.py"], "verify": ["echo ok"], "depends_on": ["T-2"]},
                {"id": "T-2", "title": "B", "files": ["b.py"], "verify": ["echo ok"], "depends_on": ["T-1"]},
            ],
        }
        state_path, _ = _make_state(tmp_path, cycle_tasks)
        sha_before = _sha256(state_path)

        with pytest.raises((ValueError, SystemExit)):
            expand_plan.expand_plan(state_path)

        assert _sha256(state_path) == sha_before, "state.yaml was mutated on cycle error"

    def test_unknown_depends_on_raises_and_state_unchanged(self, tmp_path):
        """Unknown depends_on in tasks.yaml raises; state.yaml unchanged."""
        bad_tasks = {
            "version": 1,
            "tasks": [
                {"id": "T-1", "title": "A", "files": ["a.py"], "verify": ["echo ok"], "depends_on": ["T-99"]},
            ],
        }
        state_path, _ = _make_state(tmp_path, bad_tasks)
        sha_before = _sha256(state_path)

        with pytest.raises((ValueError, SystemExit)):
            expand_plan.expand_plan(state_path)

        assert _sha256(state_path) == sha_before, "state.yaml was mutated on unknown-dep error"

    def test_missing_required_field_raises(self, tmp_path):
        """Missing required field (id) raises with field name and task position."""
        bad_tasks = {
            "version": 1,
            "tasks": [
                {"title": "No id", "files": ["a.py"], "verify": ["echo ok"]},
            ],
        }
        state_path, _ = _make_state(tmp_path, bad_tasks)

        with pytest.raises((ValueError, SystemExit)):
            expand_plan.expand_plan(state_path)

    def test_missing_tasks_yaml_raises(self, tmp_path):
        """expand_plan raises when tasks.yaml does not exist."""
        spec_changes = tmp_path / "spec" / "changes" / "no-tasks"
        spec_changes.mkdir(parents=True)
        state = {
            "change_id": "no-tasks",
            "phase": "implement",
            "schema": "feature",
            "repo_root": str(tmp_path),
            "worktree_path": str(tmp_path),
            "workflow_plan": {
                "implement": {
                    "nodes": [
                        {"id": "run-phase-review", "status": "pending",
                         "agent": "reviewer", "goal": "", "inputs": [],
                         "outputs": [], "rules": []},
                    ],
                }
            },
            "step_history": [],
        }
        state_path = tmp_path / "state.yaml"
        state_path.write_text(yaml.safe_dump(state, sort_keys=False))

        with pytest.raises((FileNotFoundError, SystemExit)):
            expand_plan.expand_plan(str(state_path))
