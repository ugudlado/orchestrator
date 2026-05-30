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
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

_FEATURE_SCHEMA = os.path.join(_REPO_ROOT, "config", "workflows", "feature.yaml")
_BUGFIX_SCHEMA = os.path.join(_REPO_ROOT, "config", "workflows", "bugfix.yaml")


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


def _load_schema_steps(schema_path: str) -> list:
    with open(schema_path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    steps = doc.get("steps") or []
    return [s["id"] if isinstance(s, dict) else str(s) for s in steps]


def _step_index(steps: list, step_id: str) -> int:
    try:
        return steps.index(step_id)
    except ValueError:
        return -1


def _make_state_with_execute_tasks_anchor(tmp_path, tasks_yaml_content=None) -> tuple[str, str]:
    """Like _make_state but includes execute-tasks anchor in the seeded plan."""
    state_path, tasks_path = _make_state(tmp_path, tasks_yaml_content)
    raw = _load_state(state_path)
    nodes = raw["workflow_plan"]["implement"]["nodes"]
    # Insert execute-tasks between expand-plan and run-phase-review.
    rpr_index = next(i for i, n in enumerate(nodes) if n.get("id") == "run-phase-review")
    nodes.insert(
        rpr_index,
        {
            "id": "execute-tasks",
            "status": "pending",
            "agent": None,
            "goal": "Execute implementation tasks",
            "inputs": [],
            "outputs": [],
            "rules": [],
            "depends_on": ["expand-plan"],
        },
    )
    # Schema declares run-phase-review depends on execute-tasks (not rewired by expand_plan).
    for node in nodes:
        if node.get("id") == "run-phase-review":
            node["depends_on"] = ["execute-tasks"]
            break
    with open(state_path, "w") as f:
        yaml.safe_dump(raw, f, sort_keys=False, default_flow_style=False)
    return state_path, tasks_path


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


# ---------------------------------------------------------------------------
# ORC-108 T-1: execute-tasks anchor in feature/bugfix schemas (RED)
# ---------------------------------------------------------------------------

_EXECUTE_TASKS_XFAIL = pytest.mark.xfail(
    reason="ORC-108 RED: execute-tasks anchor not in schemas / expand_plan still targets run-phase-review",
    strict=False,
)


class TestExecuteTasksSchemaAnchor:
    """feature.yaml and bugfix.yaml declare execute-tasks between expand-plan and run-phase-review."""

    @_EXECUTE_TASKS_XFAIL
    def test_feature_yaml_execute_tasks_between_expand_plan_and_run_phase_review(self):
        steps = _load_schema_steps(_FEATURE_SCHEMA)
        expand_idx = _step_index(steps, "expand-plan")
        execute_idx = _step_index(steps, "execute-tasks")
        rpr_idx = _step_index(steps, "run-phase-review")
        assert expand_idx >= 0, "expand-plan missing from feature.yaml"
        assert execute_idx >= 0, "execute-tasks missing from feature.yaml"
        assert rpr_idx >= 0, "run-phase-review missing from feature.yaml"
        assert expand_idx < execute_idx < rpr_idx, (
            f"execute-tasks must sit between expand-plan and run-phase-review; "
            f"got indices expand-plan={expand_idx}, execute-tasks={execute_idx}, "
            f"run-phase-review={rpr_idx}"
        )

    @_EXECUTE_TASKS_XFAIL
    def test_bugfix_yaml_execute_tasks_between_expand_plan_and_run_phase_review(self):
        steps = _load_schema_steps(_BUGFIX_SCHEMA)
        expand_idx = _step_index(steps, "expand-plan")
        execute_idx = _step_index(steps, "execute-tasks")
        rpr_idx = _step_index(steps, "run-phase-review")
        assert expand_idx >= 0, "expand-plan missing from bugfix.yaml"
        assert execute_idx >= 0, "execute-tasks missing from bugfix.yaml"
        assert rpr_idx >= 0, "run-phase-review missing from bugfix.yaml"
        assert expand_idx < execute_idx < rpr_idx, (
            f"execute-tasks must sit between expand-plan and run-phase-review; "
            f"got indices expand-plan={expand_idx}, execute-tasks={execute_idx}, "
            f"run-phase-review={rpr_idx}"
        )


class TestExecuteTasksExpandPlanInjection:
    """expand_plan.py injects under execute-tasks anchor; does not mutate run-phase-review."""

    @_EXECUTE_TASKS_XFAIL
    def test_injects_task_nodes_before_execute_tasks_anchor(self, tmp_path):
        """Task nodes precede execute-tasks, not run-phase-review."""
        state_path, _ = _make_state_with_execute_tasks_anchor(tmp_path)
        expand_plan.expand_plan(state_path)
        raw = _load_state(state_path)
        nodes = raw["workflow_plan"]["implement"]["nodes"]
        ids = [n["id"] for n in nodes]
        execute_idx = ids.index("execute-tasks")
        rpr_idx = ids.index("run-phase-review")
        task_indices = [ids.index(tid) for tid in ("task-T-1", "task-T-2", "task-T-3")]
        assert all(idx < execute_idx for idx in task_indices), (
            "task nodes must be injected before execute-tasks anchor"
        )
        assert execute_idx < rpr_idx, "execute-tasks must precede run-phase-review"
        assert all(idx < rpr_idx for idx in task_indices), (
            "task nodes must not be injected immediately before run-phase-review"
        )

    @_EXECUTE_TASKS_XFAIL
    def test_rewires_execute_tasks_depends_on_to_last_task(self, tmp_path):
        """execute-tasks.depends_on becomes [task-T-last] after injection."""
        state_path, _ = _make_state_with_execute_tasks_anchor(tmp_path)
        expand_plan.expand_plan(state_path)
        raw = _load_state(state_path)
        nodes = {n["id"]: n for n in raw["workflow_plan"]["implement"]["nodes"]}
        assert nodes["execute-tasks"]["depends_on"] == ["task-T-3"]

    @_EXECUTE_TASKS_XFAIL
    def test_execute_tasks_run_phase_review_depends_on_not_mutated(self, tmp_path):
        """run-phase-review.depends_on stays schema-declared; expand_plan does not rewire it."""
        state_path, _ = _make_state_with_execute_tasks_anchor(tmp_path)
        rpr_before = _load_state(state_path)["workflow_plan"]["implement"]["nodes"]
        rpr_deps_before = next(
            n.get("depends_on") for n in rpr_before if n.get("id") == "run-phase-review"
        )
        expand_plan.expand_plan(state_path)
        raw = _load_state(state_path)
        nodes = {n["id"]: n for n in raw["workflow_plan"]["implement"]["nodes"]}
        assert nodes["run-phase-review"].get("depends_on") == rpr_deps_before
        assert nodes["run-phase-review"]["depends_on"] == ["execute-tasks"]

    @_EXECUTE_TASKS_XFAIL
    def test_execute_tasks_idempotency_no_duplicate_nodes(self, tmp_path):
        """Second expand-plan invocation does not duplicate task nodes."""
        state_path, _ = _make_state_with_execute_tasks_anchor(tmp_path)
        expand_plan.expand_plan(state_path)
        sha1 = _sha256(state_path)
        expand_plan.expand_plan(state_path)
        sha2 = _sha256(state_path)
        assert sha1 == sha2, "Second expand-plan invocation mutated state.yaml"
        raw = _load_state(state_path)
        ids = [n["id"] for n in raw["workflow_plan"]["implement"]["nodes"]]
        for tid in ("task-T-1", "task-T-2", "task-T-3"):
            assert ids.count(tid) == 1, f"{tid} duplicated after second expand-plan run"
