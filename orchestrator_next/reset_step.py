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

import os
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


def apply_dag_reset(
    state_raw: dict[str, Any],
    phase: str,
    from_step_id: str,
    *,
    keep_history_for: str | None = None,
) -> list[str]:
    """Reset ``from_step_id`` and all later nodes in-place; strip their history.

    Returns the list of reset step ids (declaration order from target forward).
    When ``keep_history_for`` is set, history rows for that step_id in ``phase``
    are retained (so a just-recorded gate failure stays auditable).
    """
    workflow_plan = state_raw.get("workflow_plan") or {}
    phase_plan = workflow_plan.get(phase) or {}
    nodes: list[dict] = phase_plan.get("nodes") or []
    if not nodes:
        raise ValueError(f"No nodes found in workflow_plan[{phase!r}]")

    reset_ids_list = _nodes_from_index_forward(nodes, from_step_id)
    reset_ids = set(reset_ids_list)

    for node in nodes:
        if isinstance(node, dict) and str(node.get("id", "")) in reset_ids:
            node["status"] = "pending"

    history: list[Any] = state_raw.get("step_history") or []
    keep = keep_history_for or ""
    state_raw["step_history"] = [
        e
        for e in history
        if not (
            isinstance(e, dict)
            and e.get("phase") == phase
            and e.get("step_id") in reset_ids
            and str(e.get("step_id") or "") != keep
        )
    ]

    next_step = state_raw.get("next_step")
    if isinstance(next_step, dict) and next_step.get("step_id") in reset_ids:
        state_raw.pop("next_step", None)

    return reset_ids_list


def reset_step(step_id: str, state_yaml_path: str) -> None:
    """Reset step_id and all subsequent nodes to pending; strip their step_history entries."""
    path = Path(state_yaml_path)

    try:
        state_raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Failed to parse state.yaml: {exc}") from exc

    phase = str(state_raw.get("phase") or "implement")
    apply_dag_reset(state_raw, phase, step_id)
    state_raw["status"] = "active"

    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(
        yaml.safe_dump(state_raw, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    os.replace(tmp_path, path)
