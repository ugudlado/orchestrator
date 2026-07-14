"""
Mermaid DAG renderer for the orchestrator engine (ORC-63).

`render_workflow_graph(schema_name)` returns a static Mermaid `flowchart TD`
of a workflow schema's step topology — linear chain + conditional routing
edges (on_success / on_failure).

Read-only: no state mutation.
"""
from __future__ import annotations

import re
from typing import Any

import yaml


def _safe_id(node_id: str) -> str:
    """Return a Mermaid-safe node identifier (alphanumerics + underscore)."""
    return re.sub(r"[^A-Za-z0-9_]", "_", node_id) or "node"


def _normalize_steps(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a flat list of step dicts from a schema's top-level steps list."""
    raw_steps = schema.get("steps", [])
    result: list[dict[str, Any]] = []
    for entry in raw_steps:
        if isinstance(entry, str):
            result.append({"id": entry.strip()})
        elif isinstance(entry, dict):
            result.append(entry)
    return result


def _load_wf_schema(schema_name: str) -> dict[str, Any]:
    """Load config/workflows/<name>.yaml or raise FileNotFoundError."""
    from orchestrator_next.paths import config_root

    schema_path = config_root() / "workflows" / f"{schema_name}.yaml"
    if not schema_path.is_file():
        raise FileNotFoundError(f"Workflow schema not found: {schema_path}")
    with open(schema_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def render_workflow_graph(schema_name: str) -> str:
    """Render a workflow schema's step topology as a Mermaid `flowchart TD` string."""
    schema = _load_wf_schema(schema_name)
    steps = _normalize_steps(schema)
    lines = ["flowchart TD", f"  %% workflow: {schema_name}"]

    if not steps:
        lines.append('  empty["(no steps)"]')
        return "\n".join(lines) + "\n"

    for step in steps:
        sid = str(step.get("id", ""))
        lines.append(f'  {_safe_id(sid)}["{sid}"]')

    step_ids = [str(s.get("id", "")) for s in steps]
    for idx, step in enumerate(steps):
        sid = str(step.get("id", ""))
        on_success = step.get("on_success")
        on_failure = step.get("on_failure")
        next_sid = step_ids[idx + 1] if idx + 1 < len(step_ids) else None

        if on_success or on_failure:
            if on_success and on_success != next_sid:
                lines.append(f"  {_safe_id(sid)} -->|success| {_safe_id(on_success)}")
            elif on_success and on_success == next_sid:
                lines.append(f"  {_safe_id(sid)} --> {_safe_id(on_success)}")
            if on_failure:
                lines.append(f"  {_safe_id(sid)} -->|retry| {_safe_id(on_failure)}")
            if not on_success and next_sid:
                lines.append(f"  {_safe_id(sid)} --> {_safe_id(next_sid)}")
        elif next_sid:
            lines.append(f"  {_safe_id(sid)} --> {_safe_id(next_sid)}")

    return "\n".join(lines) + "\n"
