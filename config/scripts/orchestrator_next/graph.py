"""
Mermaid DAG renderer for the orchestrator engine (ORC-63).

`render_graph(state)` returns a Mermaid `flowchart TD` of the current phase's
node graph — one node per `workflow_plan[phase].nodes` entry, labelled by
status, with an edge per effective `depends_on` relationship.

Read-only: no state mutation, no DuckDB.
"""
from __future__ import annotations

import re

from orchestrator_next.parser import State, phase_nodes
from orchestrator_next import readiness


def _safe_id(node_id: str) -> str:
    """Return a Mermaid-safe node identifier (alphanumerics + underscore)."""
    return re.sub(r"[^A-Za-z0-9_]", "_", node_id) or "node"


def render_graph(state: State) -> str:
    """Render the current phase's DAG as a Mermaid `flowchart TD` string."""
    phase = state.phase
    nodes = phase_nodes(state, phase)
    lines = [f"flowchart TD"]
    lines.append(f"  %% phase: {phase}")

    if not nodes:
        lines.append("  empty[\"(no nodes)\"]")
        return "\n".join(lines) + "\n"

    # One node declaration per plan node, labelled "<id> [<status>]".
    for node in nodes:
        nid = str(node.get("id", ""))
        status = str(node.get("status", "pending"))
        lines.append(f'  {_safe_id(nid)}["{nid} [{status}]"]')

    # One edge per effective depends_on relationship.
    for node in nodes:
        nid = str(node.get("id", ""))
        for dep in readiness.effective_depends_on(nodes, nid):
            lines.append(f"  {_safe_id(dep)} --> {_safe_id(nid)}")

    return "\n".join(lines) + "\n"
