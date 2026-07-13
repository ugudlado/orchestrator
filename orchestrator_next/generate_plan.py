"""
Promote a seeded state.yaml workflow_plan into the DAG `nodes` shape (ORC-63).

Public API: generate_plan(state_yaml_path: str) -> None
Reads state.yaml + schema, topo-sorts the dependency graph, and rewrites
state.yaml in place with `workflow_plan[phase] = {nodes, filtered, verify}`.
No separate plan file is produced — workflow state lives in one file.

Entry point: python -m orchestrator_next.generate_plan <state_yaml_path>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

from orchestrator_next.paths import config_root


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_schema(schema_name: str) -> dict[str, Any]:
    """Load a workflow schema YAML by name."""
    path = config_root() / "workflows" / f"{schema_name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"Schema file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _resolve_phases(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Return a flat list of fully-resolved phase dicts from a schema.

    Phase-less schemas (top-level `steps:` with no `phases:`) synthesize a
    single phase named "main"; the rest of the engine treats them identically
    to legacy multi-phase schemas.
    """
    raw_phases = schema.get("phases")
    if not raw_phases:
        steps = schema.get("steps")
        if not steps:
            return []
        synthetic: dict[str, Any] = {
            "name": "main",
            "goal": schema.get("description", ""),
            "steps": steps,
        }
        for key in ("verify", "outputs", "rules"):
            if key in schema:
                synthetic[key] = schema[key]
        return [synthetic]

    return list(raw_phases)


def _find_phase_def(phases: list[dict[str, Any]], phase_name: str) -> dict[str, Any]:
    """Return the phase dict for a given phase name, or raise."""
    for p in phases:
        if p.get("name") == phase_name:
            return p
    raise ValueError(f"Phase '{phase_name}' not found in resolved schema phases")


def _step_entry_for_id(phase_def: dict[str, Any], step_id: str) -> dict[str, Any] | None:
    """
    Find the schema step entry matching step_id in a phase definition.

    Step entries may be:
    - Plain string: "design-and-draft-artifacts"
    - String with gate: "explore if discovery"  (strip " if <flag>")
    - Dict: {id: ..., depends_on: ..., on_success: ...}

    Returns the dict form or an empty dict if the step is a plain string match.
    Returns None if no match found.
    """
    for entry in phase_def.get("steps", []):
        if isinstance(entry, dict):
            if entry.get("id") == step_id:
                return entry
        elif isinstance(entry, str):
            bare = entry.split(" if ")[0].strip()
            if bare == step_id:
                return {}
    return None


def _build_step_node(step_id: str, phase_def: dict[str, Any]) -> dict[str, Any]:
    """Build the per-step node block: id, status, and optional depends_on/routing."""
    step_entry = _step_entry_for_id(phase_def, step_id) or {}

    node: dict[str, Any] = {
        "id": step_id,
        "status": "pending",
    }

    authored = step_entry.get("depends_on")
    if authored:
        node["depends_on"] = [str(d) for d in authored]

    for edge_key in ("on_success", "on_failure", "max_retries"):
        val = step_entry.get(edge_key)
        if val is not None:
            node[edge_key] = val

    return node


def _topo_sort(nodes: list[dict[str, Any]], filtered_ids: set[str]) -> None:
    """Validate the node DAG via Kahn's algorithm (ORC-63).

    Builds the effective edge set: each node's authored `depends_on`, else an
    implicit chain edge on its declaration-order predecessor. Edges that
    target a `filtered` step are dropped in place (with a stderr warning).
    Edges that target an unknown id (not a node, not filtered) raise
    ValueError. A cycle raises ValueError naming the cycle path.

    Mutates each node's `depends_on` to its effective edge set so the promoted
    state.yaml carries explicit edges. The first node keeps no implicit edge.
    """
    node_ids = [str(n.get("id", "")) for n in nodes]
    id_set = set(node_ids)

    effective: dict[str, list[str]] = {}
    for idx, node in enumerate(nodes):
        nid = str(node.get("id", ""))
        authored = node.get("depends_on")
        if authored is not None:
            deps = [str(d) for d in authored]
        elif idx > 0:
            deps = [node_ids[idx - 1]]
        else:
            deps = []

        kept: list[str] = []
        for dep in deps:
            if dep in filtered_ids:
                print(
                    f"WARNING: step {nid!r} depends_on filtered step {dep!r} — "
                    f"dropping the edge",
                    file=sys.stderr,
                )
                continue
            if dep not in id_set:
                raise ValueError(
                    f"step {nid!r} depends_on unknown step {dep!r} — "
                    f"not a node in this phase and not filtered"
                )
            kept.append(dep)
        effective[nid] = kept
        if kept:
            node["depends_on"] = kept
        elif "depends_on" in node:
            del node["depends_on"]

    # Kahn's algorithm: detect a cycle.
    indegree = {nid: 0 for nid in node_ids}
    adj: dict[str, list[str]] = {nid: [] for nid in node_ids}
    for nid, deps in effective.items():
        for dep in deps:
            adj[dep].append(nid)
            indegree[nid] += 1

    queue = [nid for nid in node_ids if indegree[nid] == 0]
    visited = 0
    while queue:
        cur = queue.pop(0)
        visited += 1
        for nxt in adj[cur]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)

    if visited != len(node_ids):
        in_cycle = sorted(nid for nid in node_ids if indegree[nid] > 0)
        raise ValueError(
            f"dependency cycle detected among steps: {' -> '.join(in_cycle)}"
        )


def _write_yaml_stable(obj: Any, path: Path) -> None:
    content = yaml.safe_dump(obj, sort_keys=False, default_flow_style=False, allow_unicode=True)
    path.write_text(content, encoding="utf-8")


def generate_plan(state_yaml_path: str) -> None:
    """
    Read state.yaml, promote each phase's `active:[ids]` list into a
    `nodes:[{...}]` graph, and rewrite state.yaml in place (ORC-63).

    `workflow_plan[phase]` becomes `{nodes:[...], filtered, verify}`; the
    `active` key is removed. Topo-sort detects cycles before any write — on a
    cycle, state.yaml is left untouched. No plan.yaml is produced.

    Public API. Returns None. Raises on unrecoverable errors.
    """
    from orchestrator_next import parser as _parser
    state = _parser.load_state(state_yaml_path)
    schema_name = state.raw.get("schema", "")

    schema = _load_schema(schema_name)
    resolved_phases = _resolve_phases(schema)

    promoted: dict[str, Any] = {}
    for phase_name, phase_plan in state.raw.get("workflow_plan", {}).items():
        if isinstance(phase_plan, dict):
            if "nodes" in phase_plan:
                active_step_ids = [
                    str(n.get("id", ""))
                    for n in (phase_plan.get("nodes") or [])
                ]
            else:
                active_step_ids = list(phase_plan.get("active", []))
            filtered = phase_plan.get("filtered", []) or []
        else:
            active_step_ids = []
            filtered = []

        try:
            phase_def = _find_phase_def(resolved_phases, phase_name)
        except ValueError as e:
            print(f"WARNING: {e} — skipping phase", file=sys.stderr)
            promoted[phase_name] = phase_plan
            continue

        nodes = [_build_step_node(step_id, phase_def) for step_id in active_step_ids]

        filtered_ids = {
            (f.get("id") if isinstance(f, dict) else str(f))
            for f in filtered
        }
        _topo_sort(nodes, filtered_ids)

        verify_block = phase_plan.get("verify") if isinstance(phase_plan, dict) else None
        if verify_block is None:
            base_verify = phase_def.get("verify")
            if base_verify is not None:
                verify_block = base_verify

        phase_block: dict[str, Any] = {"nodes": nodes, "filtered": filtered}
        if verify_block:
            phase_block["verify"] = verify_block
        promoted[phase_name] = phase_block

    state_raw = dict(state.raw)
    state_raw["workflow_plan"] = promoted
    _write_yaml_stable(state_raw, Path(state_yaml_path))
    print(f"state.yaml workflow_plan promoted to nodes shape: {state_yaml_path}", file=sys.stderr)


def main() -> None:
    """Entry point: python -m orchestrator_next.generate_plan <state_yaml_path>."""
    ap = argparse.ArgumentParser(
        description="Promote state.yaml workflow_plan into the DAG nodes shape",
    )
    ap.add_argument("state_yaml_path", help="Path to state.yaml")
    args = ap.parse_args()
    generate_plan(args.state_yaml_path)


if __name__ == "__main__":
    main()
