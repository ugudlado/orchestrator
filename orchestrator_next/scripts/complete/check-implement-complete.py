#!/usr/bin/env python3
"""Implement-completeness guard for `orchestrator complete`.

Verifies all non-complete-phase nodes are completed/skipped, marks any
remaining implement nodes completed, and advances next_step to the first
incomplete complete-phase step. Complete-phase nodes are declared in the
feature/bugfix schema tail (no runtime DAG injection).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

from orchestrator_next.operator_workflow import ensure_orchestrator_home, workflow_step_ids

_COMPLETE_ANCHOR = "compute-prediction-accuracy"


def _complete_step_ids(schema_name: str) -> list[str]:
    steps = workflow_step_ids(schema_name)
    if _COMPLETE_ANCHOR not in steps:
        raise ValueError(
            f"schema {schema_name!r} has no {_COMPLETE_ANCHOR!r} step; "
            "cannot run complete phase"
        )
    return steps[steps.index(_COMPLETE_ANCHOR) :]


def check_implement_complete(state_yaml: str) -> dict[str, Any]:
    ensure_orchestrator_home()
    path = Path(state_yaml)
    pre = path.read_bytes()
    state = yaml.safe_load(pre.decode("utf-8")) or {}
    if not isinstance(state, dict):
        raise ValueError("state.yaml must be a mapping")
    if state.get("status") == "completed":
        # Allow re-entry when DAG nodes are still pending (e.g. archived state where
        # merge-to-main / remove-worktree never ran). Check the actual nodes rather
        # than cross-referencing the current schema — the seeded DAG may predate
        # schema changes (e.g. ORC-108 removed complete-phase steps from feature.yaml).
        phase_check = str(state.get("phase") or "main")
        nodes_check = (state.get("workflow_plan") or {}).get(phase_check, {}).get("nodes") or []
        has_pending = any(
            isinstance(n, dict)
            and str(n.get("status") or "pending") not in ("completed", "skipped")
            for n in nodes_check
        )
        if not has_pending:
            raise ValueError("workflow already completed")

    schema = str(state.get("schema") or "")
    if not schema:
        raise ValueError("state.yaml missing schema")

    phase = str(state.get("phase") or "main")
    nodes = (state.get("workflow_plan") or {}).get(phase, {}).get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError(f"workflow_plan.{phase}.nodes is missing or empty")

    # Derive complete-phase step ids from the schema if available; fall back to
    # scanning the DAG for the anchor when the schema has drifted (e.g. feature.yaml
    # no longer carries complete-phase steps after ORC-108).
    try:
        complete_steps_ordered = _complete_step_ids(schema)
    except ValueError:
        node_ids = [str(n.get("id") or "") for n in nodes if isinstance(n, dict)]
        if _COMPLETE_ANCHOR not in node_ids:
            raise
        complete_steps_ordered = node_ids[node_ids.index(_COMPLETE_ANCHOR):]
    complete_ids = set(complete_steps_ordered)

    blocked: list[str] = []
    auto_completed: list[str] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        nid = str(node.get("id") or "")
        status = str(node.get("status") or "pending")
        if nid in complete_ids:
            continue
        if status in ("completed", "skipped"):
            continue
        if nid.startswith("task-"):
            blocked.append(f"task node {nid} is {status}")
        else:
            blocked.append(f"step {nid} is {status}")
    if blocked:
        raise ValueError(
            "implement phase must finish before complete: " + "; ".join(blocked)
        )

    for node in nodes:
        if not isinstance(node, dict):
            continue
        nid = str(node.get("id") or "")
        if nid in complete_ids:
            continue
        if str(node.get("status") or "") != "completed":
            node["status"] = "completed"
            auto_completed.append(nid)

    next_id = None
    for sid in complete_steps_ordered:
        for node in nodes:
            if isinstance(node, dict) and str(node.get("id") or "") == sid:
                if str(node.get("status") or "") != "completed":
                    next_id = sid
                break
        if next_id:
            break

    if next_id:
        state["next_step"] = {"phase": phase, "step_id": next_id}
    else:
        state.pop("next_step", None)
    state["status"] = "active"

    try:
        with path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(state, f, sort_keys=False, default_flow_style=False)
        with path.open(encoding="utf-8") as f:
            yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as exc:
        path.write_bytes(pre)
        raise ValueError(f"failed to write state.yaml: {exc}") from exc

    return {
        "change_id": state.get("change_id") or state.get("slug"),
        "schema": schema,
        "complete_steps": sorted(complete_ids),
        "auto_completed_nodes": auto_completed,
        "next_step": next_id,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check implement phase complete and advance to complete tail"
    )
    parser.add_argument("state_yaml", help="Path to state.yaml")
    args = parser.parse_args(argv)
    try:
        summary = check_implement_complete(args.state_yaml)
    except (ValueError, FileNotFoundError, OSError, EnvironmentError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(yaml.safe_dump(summary, sort_keys=False), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
