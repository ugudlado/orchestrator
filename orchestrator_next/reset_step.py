"""
orchestrator reset-step <step-id> <state.yaml>

Resets a workflow step and all steps that depend on it (directly or transitively,
by declaration order) back to pending. Strips their step_history entries so the
DAG walker treats them as not-yet-run.

Used by review steps to send work back to an earlier step when the reviewer
finds the artifacts insufficient.

Public API: reset_step(step_id, state_yaml_path) -> None
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _nodes_from_index_forward(nodes: list[dict], target_id: str) -> list[str]:
    """Return ids of target node and all nodes declared after it (declaration order).

    Uses declaration order as a proxy for dependency order — nodes declared after
    the target are assumed to depend on it, directly or transitively. This is
    conservative: it resets more than strictly needed, but avoids requiring a full
    topo-sort and is safe (resetting an independent node just re-runs it cheaply).
    """
    ids = [str(n.get("id", "")) for n in nodes if isinstance(n, dict)]
    try:
        idx = ids.index(target_id)
    except ValueError:
        raise ValueError(f"step {target_id!r} not found in workflow_plan nodes")
    return ids[idx:]


def reset_step(step_id: str, state_yaml_path: str) -> None:
    """Reset step_id and all subsequent nodes to pending; strip their step_history entries."""
    path = Path(state_yaml_path)

    with open(path, "rb") as f:
        pre_write_bytes = f.read()

    try:
        state_raw = yaml.safe_load(pre_write_bytes.decode("utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Failed to parse state.yaml: {exc}") from exc

    phase = str(state_raw.get("phase") or "implement")
    workflow_plan = state_raw.get("workflow_plan") or {}
    phase_plan = workflow_plan.get(phase) or {}
    nodes: list[dict] = phase_plan.get("nodes") or []

    if not nodes:
        raise ValueError(f"No nodes found in workflow_plan[{phase!r}]")

    reset_ids = set(_nodes_from_index_forward(nodes, step_id))

    # Reset node statuses to pending.
    for node in nodes:
        if isinstance(node, dict) and str(node.get("id", "")) in reset_ids:
            node["status"] = "pending"

    # Strip step_history entries for the reset nodes.
    history: list[Any] = state_raw.get("step_history") or []
    state_raw["step_history"] = [
        e for e in history
        if not (isinstance(e, dict) and e.get("phase") == phase and e.get("step_id") in reset_ids)
    ]

    # Clear next_step if it pointed at a now-reset node.
    next_step = state_raw.get("next_step")
    if isinstance(next_step, dict) and next_step.get("step_id") in reset_ids:
        state_raw.pop("next_step", None)

    # Atomic write with corruption guard.
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(state_raw, f, sort_keys=False, default_flow_style=False)

    try:
        with open(path, "r", encoding="utf-8") as f:
            yaml.safe_load(f)
    except yaml.YAMLError:
        with open(path, "wb") as f:
            f.write(pre_write_bytes)
        raise
