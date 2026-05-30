"""
ORC-65: orchestrator expand-plan — append flat task-nodes to workflow_plan.

Public API: expand_plan(state_yaml_path: str) -> None

Reads tasks.yaml from the worktree artifact dir, builds one task-node per task
(id=`task-<task_id>`, agent: developer, step_contract: execute-one-task,
depends_on mapped through `task-` prefix, task: payload), appends only ids not
already present in workflow_plan[implement].nodes, rewires run-phase-review's
depends_on to the last task-node id.

Uses generate_plan._topo_sort for cycle/unknown-id validation over the full
plan. Writes state.yaml atomically (pre-write byte buffer; restores on parse
failure).

Entry point: python -m orchestrator_next.expand_plan <state_yaml_path>
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_REQUIRED_TASK_FIELDS = ("id", "title", "files", "verify")


def _validate_tasks(tasks: list[dict]) -> None:
    """Raise ValueError for missing required fields or duplicate ids."""
    seen_ids: set[str] = set()
    for i, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise ValueError(f"Task at index {i} is not a mapping")
        for field in _REQUIRED_TASK_FIELDS:
            if field not in task:
                task_id = task.get("id", f"<index {i}>")
                raise ValueError(f"Task '{task_id}' missing required field '{field}'")
        task_id = task.get("id")
        if task_id in seen_ids:
            raise ValueError(f"Duplicate task id '{task_id}'")
        seen_ids.add(task_id)

    # Validate depends_on references
    for task in tasks:
        deps = task.get("depends_on") or []
        if not isinstance(deps, list):
            raise ValueError(
                f"Task '{task.get('id')}' depends_on must be a list, got {type(deps).__name__}"
            )
        for dep in deps:
            if dep not in seen_ids:
                raise ValueError(
                    f"Task '{task.get('id')}' depends_on unknown task id '{dep}'"
                )


# ---------------------------------------------------------------------------
# Node builder
# ---------------------------------------------------------------------------

def _build_task_node(task: dict) -> dict[str, Any]:
    """Build a workflow_plan node dict from a tasks.yaml task entry."""
    # Map depends_on: T-N → task-T-N
    # Always set depends_on explicitly (even as []) so _topo_sort does not apply
    # implicit chain edges to task-nodes.
    raw_deps = task.get("depends_on") or []
    mapped_deps = [f"task-{dep}" for dep in raw_deps]

    node: dict[str, Any] = {
        "id": f"task-{task['id']}",
        "status": "pending",
        "agent": task.get("agent", "developer"),
        "step_contract": "execute-one-task",
        "goal": task["title"],
        "inputs": [],
        "outputs": ["task_execution_result"],
        "rules": [],
        "depends_on": mapped_deps,  # always explicit; empty list = no deps
        "task": {k: v for k, v in task.items()},
    }
    return node


# ---------------------------------------------------------------------------
# Core expand function
# ---------------------------------------------------------------------------

def expand_plan(state_yaml_path: str) -> None:
    """Read tasks.yaml, append task-nodes to workflow_plan[implement].nodes.

    Idempotent: nodes whose id is already present are skipped.
    Rewires run-phase-review.depends_on to the last task-node id.
    Validates the full plan with _topo_sort before writing.
    Atomic write: restores pre-write bytes on YAML parse failure.
    """
    path = Path(state_yaml_path)

    # Pre-read for atomic write guard.
    with open(path, "rb") as f:
        pre_write_bytes = f.read()

    try:
        state_raw = yaml.safe_load(pre_write_bytes.decode("utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Failed to parse state.yaml: {exc}") from exc

    # Resolve tasks.yaml path from worktree_artifact_dir + change_id.
    change_id = state_raw.get("change_id") or ""
    worktree_path = state_raw.get("worktree_path") or state_raw.get("repo_root") or ""
    if worktree_path.startswith("~"):
        worktree_path = os.path.expanduser(worktree_path)

    artifact_dir = os.path.join(worktree_path, "spec", "changes", change_id) if worktree_path and change_id else ""
    tasks_yaml_path = Path(artifact_dir) / "tasks.yaml" if artifact_dir else None

    if tasks_yaml_path is None or not tasks_yaml_path.is_file():
        raise FileNotFoundError(
            f"tasks.yaml not found at {tasks_yaml_path}. "
            "Run design-and-draft-artifacts to produce it."
        )

    with open(tasks_yaml_path, "r", encoding="utf-8") as f:
        tasks_doc = yaml.safe_load(f)

    if not isinstance(tasks_doc, dict):
        raise ValueError(f"tasks.yaml must be a YAML mapping, got {type(tasks_doc).__name__}")

    tasks = tasks_doc.get("tasks")
    if not isinstance(tasks, list):
        raise ValueError("tasks.yaml 'tasks' must be a list")

    # Validate tasks structure (raises ValueError on bad input).
    _validate_tasks(tasks)

    # Get current plan nodes.
    phase = state_raw.get("phase") or "implement"
    workflow_plan = state_raw.setdefault("workflow_plan", {})
    phase_plan = workflow_plan.setdefault(phase, {})
    nodes: list[dict] = phase_plan.setdefault("nodes", [])
    filtered_ids: set[str] = {
        (f.get("id") if isinstance(f, dict) else str(f))
        for f in (phase_plan.get("filtered") or [])
    }

    existing_ids = {str(n.get("id", "")) for n in nodes}

    # Find the insertion point: just before run-phase-review (if present),
    # otherwise at the end. Task nodes must precede run-phase-review in the
    # declaration order so _topo_sort's implicit chaining doesn't mis-wire edges.
    rpr_index = None
    for i, n in enumerate(nodes):
        if str(n.get("id", "")) == "run-phase-review":
            rpr_index = i
            break

    # Insert new task-nodes in order, each before run-phase-review.
    insert_at = rpr_index if rpr_index is not None else len(nodes)
    for task in tasks:
        node_id = f"task-{task['id']}"
        if node_id in existing_ids:
            continue
        node = _build_task_node(task)
        nodes.insert(insert_at, node)
        existing_ids.add(node_id)
        insert_at += 1  # keep order; next insert goes after this one

    # Rewire run-phase-review.depends_on to the last task-node id.
    task_node_ids = [
        str(n.get("id", ""))
        for n in nodes
        if str(n.get("id", "")).startswith("task-")
    ]
    if task_node_ids:
        last_task_node_id = task_node_ids[-1]
        for node in nodes:
            if str(node.get("id", "")) == "run-phase-review":
                node["depends_on"] = [last_task_node_id]
                break

    # Validate the full plan with topo-sort (raises ValueError on cycles/unknown ids).
    # _topo_sort mutates node dicts in place (canonicalizes depends_on). We run it
    # on a deep copy so our authoritative nodes stay unmodified and the write is
    # idempotent.
    import copy
    from orchestrator_next.generate_plan import _topo_sort
    nodes_copy = copy.deepcopy(nodes)
    _topo_sort(nodes_copy, filtered_ids)

    # If nothing was appended and run-phase-review wasn't rewired, we can skip writing.
    # But to keep logic simple, always write (yaml.safe_dump is deterministic for same input,
    # so idempotent second run will produce identical bytes only if ordering is preserved).
    # Write atomically.
    _write_state_yaml(path, state_raw, pre_write_bytes)


def _write_state_yaml(path: Path, state_raw: dict, pre_write_bytes: bytes) -> None:
    """Write state_raw to path with corruption guard."""
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(state_raw, f, sort_keys=False, default_flow_style=False)

    # Corruption guard: verify written file is parseable; restore on failure.
    try:
        with open(path, "r", encoding="utf-8") as f:
            yaml.safe_load(f)
    except yaml.YAMLError:
        with open(path, "wb") as f:
            f.write(pre_write_bytes)
        raise


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        prog="orchestrator expand-plan",
        description="Append task-nodes from tasks.yaml to workflow_plan in state.yaml.",
    )
    ap.add_argument("state_yaml", help="Path to state.yaml")
    args = ap.parse_args()

    try:
        expand_plan(args.state_yaml)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    except (ValueError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
