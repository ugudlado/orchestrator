"""Prepare state.yaml for a complete-phase-only workflow run.

`orchestrator complete <ticket>` loads active state, verifies implement work is
finished, and marks non-complete plan nodes as completed so the dispatch loop
only runs the schema tail from ``compute-prediction-accuracy`` through
``complete-workflow``.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

_COMPLETE_ANCHOR = "compute-prediction-accuracy"


def _orchestrator_home() -> Path:
    home = __import__("os").environ.get("ORCHESTRATOR_HOME", "")
    if not home:
        raise EnvironmentError("ORCHESTRATOR_HOME is not set")
    return Path(home)


def _load_schema_steps(schema_name: str) -> list[str]:
    path = _orchestrator_home() / "config" / "workflows" / f"{schema_name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"schema not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    steps: list[str] = []
    for entry in raw.get("steps") or []:
        if isinstance(entry, dict):
            steps.append(str(entry.get("id", "")))
        else:
            steps.append(str(entry))
    return [s for s in steps if s]


def complete_step_ids_for_schema(schema_name: str) -> list[str]:
    """Return ordered complete-phase step ids for a workflow schema."""
    steps = _load_schema_steps(schema_name)
    if _COMPLETE_ANCHOR not in steps:
        raise ValueError(
            f"schema {schema_name!r} has no {_COMPLETE_ANCHOR!r} step; "
            "cannot run complete phase"
        )
    idx = steps.index(_COMPLETE_ANCHOR)
    return steps[idx:]


def prepare_complete_phase(state_yaml_path: str) -> dict[str, Any]:
    """Rewrite workflow_plan so only complete-phase steps can dispatch.

    Raises ValueError when implement-phase nodes or task nodes are incomplete.
    Returns a summary dict for logging.
    """
    path = Path(state_yaml_path)
    pre = path.read_bytes()
    state = yaml.safe_load(pre.decode("utf-8")) or {}
    if not isinstance(state, dict):
        raise ValueError("state.yaml must be a mapping")

    if state.get("status") == "completed":
        raise ValueError("workflow already completed")

    schema = str(state.get("schema") or "")
    if not schema:
        raise ValueError("state.yaml missing schema")

    # The `complete` workflow file is the step list for `orchestrator complete`
    # (not the parent feature/bugfix tail — same anchor, but complete.yaml is canonical).
    complete_ids = set(complete_step_ids_for_schema("complete"))
    phase = str(state.get("phase") or "main")
    phase_plan = (state.get("workflow_plan") or {}).get(phase) or {}
    nodes = phase_plan.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError(f"workflow_plan.{phase}.nodes is missing or empty")

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
            continue
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

    # Point next_step at the first incomplete complete-phase node.
    next_id = None
    for sid in complete_step_ids_for_schema("complete"):
        for node in nodes:
            if str(node.get("id") or "") == sid:
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
        "complete_steps": list(complete_ids),
        "auto_completed_nodes": auto_completed,
        "next_step": next_id,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare state for complete phase")
    parser.add_argument("state_yaml", help="Path to state.yaml")
    args = parser.parse_args(argv)
    try:
        summary = prepare_complete_phase(args.state_yaml)
    except (ValueError, FileNotFoundError, EnvironmentError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(yaml.safe_dump(summary, sort_keys=False), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
