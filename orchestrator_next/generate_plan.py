"""
Promote a seeded state.yaml workflow_plan into the DAG `nodes` shape (ORC-63).

Public API: generate_plan(state_yaml_path: str) -> None
Reads state.yaml + schema + step contracts + project.yaml, applies the 5-tier
rule merge (rule-merge.md), topo-sorts the dependency graph, and rewrites
state.yaml in place with `workflow_plan[phase] = {nodes, filtered, verify}`.
No separate plan file is produced — workflow state lives in one file.

Entry point: python -m orchestrator_next.generate_plan <state_yaml_path>
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from orchestrator_next import parser as _parser


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_schema(schema_name: str) -> dict[str, Any]:
    """Load a workflow schema YAML by name."""
    from orchestrator_next.paths import config_root
    path = config_root() / "workflows" / f"{schema_name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"Schema file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_project(repo_root: str) -> dict[str, Any]:
    """Load spec/project.yaml from repo_root."""
    path = Path(repo_root) / "spec" / "project.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"spec/project.yaml not found at: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_step_contract_raw(step_id: str, state_yaml_path: str) -> dict[str, Any] | None:
    """
    Load raw YAML dict for a step contract.

    Returns None if not found (missing contract → emit step without rules).
    Uses the same search dirs as parser._contract_search_dirs.

    Search order mirrors parser._load_contract: for each directory, the
    directory form (<step_id>/contract.yaml) is checked before the flat-file
    form (<step_id>.yaml).
    """
    search_dirs = _parser._contract_search_dirs(state_yaml_path)
    for d in search_dirs:
        # Directory form: <d>/<step_id>/contract.yaml (preferred)
        dir_contract = os.path.join(d, step_id, "contract.yaml")
        if os.path.isfile(dir_contract):
            with open(dir_contract, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        # Flat-file form: <d>/<step_id>.yaml (back-compat)
        flat_contract = os.path.join(d, f"{step_id}.yaml")
        if os.path.isfile(flat_contract):
            with open(flat_contract, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
    print(f"WARNING: step contract not found for '{step_id}', skipping rules", file=sys.stderr)
    return None


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
        for key in ("verify", "verify_when", "outputs", "rules"):
            if key in schema:
                synthetic[key] = schema[key]
        return [synthetic]

    resolved: list[dict[str, Any]] = []
    for phase_entry in raw_phases:
        resolved.append(phase_entry)
    return resolved


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
    - Dict: {id: ..., rules_when: ..., extra_rules: ..., repeat_until: ...}

    Returns the dict form {id, rules_when, extra_rules, repeat_until}
    or an empty dict if the step is a plain string match (no injections).
    Returns None if no match found.
    """
    for entry in phase_def.get("steps", []):
        if isinstance(entry, dict):
            if entry.get("id") == step_id:
                return entry
        elif isinstance(entry, str):
            # Strip " if <flag>" suffix
            bare = entry.split(" if ")[0].strip()
            if bare == step_id:
                return {}  # plain string — no injections
    return None


def _evaluate_rules_when(rules_when: dict[str, Any], flags: dict[str, Any]) -> list[str]:
    """
    Evaluate rules_when mapping against resolved flags per rule-merge.md § Rules-When Evaluation.

    keys: "<flag>" → activate if flag truthy
          "not <flag>" → activate if flag falsy/absent

    Returns list of activated plain-string rules.
    """
    result: list[str] = []
    positive_flags: set[str] = set()
    for key, rules in rules_when.items():
        if key.startswith("not "):
            flag_name = key[4:].strip()
            flag_val = flags.get(flag_name, False)
            # Only activate if NOT already activated by positive match
            if not flag_val and flag_name not in positive_flags:
                result.extend(rules if isinstance(rules, list) else [rules])
        else:
            flag_val = flags.get(key, False)
            if flag_val:
                positive_flags.add(key)
                result.extend(rules if isinstance(rules, list) else [rules])
    return result


def _filter_step_rule(rule: str, repo_name: str) -> bool:
    """
    Return True if a plain-string rule should be kept for this repo.

    Rules with <!-- learned: ... repo: X --> metadata are scoped.
    """
    m = re.search(r"<!--\s*learned:.*?repo:\s*(\S+?)[\s>]", rule)
    if m:
        repo_field = m.group(1).rstrip(">").strip()
        return repo_field == "*" or repo_field == repo_name
    # Has learned metadata but no repo field → backward compat, keep
    if "<!-- learned:" in rule and "repo:" not in rule:
        return True
    # No metadata → permanent, keep
    return True


def _merge_rules(
    step_entry: dict[str, Any],
    contract_raw: dict[str, Any] | None,
    phase_def: dict[str, Any],
    schema: dict[str, Any],
    project: dict[str, Any],
    flags: dict[str, Any],
    repo_name: str,
) -> list[str]:
    """
    Apply 5-tier merge per rule-merge.md.

    Returns the merged ordered list of rule strings.
    """
    # ----------- Tier 1: Step entry injections -----------
    rules_when = step_entry.get("rules_when", {}) or {}
    extra_rules = step_entry.get("extra_rules", []) or []
    injected = _evaluate_rules_when(rules_when, flags)
    extra = list(extra_rules) if isinstance(extra_rules, list) else [extra_rules]

    # ----------- Tier 2: Step contract rules (filtered by repo scope) -----------
    contract_rules_raw: list[str] = []
    if contract_raw is not None:
        contract_rules_raw = contract_raw.get("rules", []) or []
    step_rules = [r for r in contract_rules_raw if _filter_step_rule(str(r), repo_name)]

    # ----------- Tier 3: Phase rules (plain strings) -----------
    phase_rules_raw = phase_def.get("rules", []) or []
    phase_rules = [str(r) for r in phase_rules_raw if isinstance(r, str)]

    # ----------- Tiers 4+5: Named rules (schema overrides project, deduped by id) -----------
    # Start with project rules, then override/add schema rules
    named_rules: dict[str, dict[str, Any]] = {}
    for entry in (project.get("rules") or []):
        if isinstance(entry, dict) and "id" in entry:
            named_rules[entry["id"]] = entry

    for entry in (schema.get("rules") or []):
        if isinstance(entry, dict) and "id" in entry:
            # Schema overrides project on same id
            named_rules[entry["id"]] = entry

    # Filter by when-condition against flags
    active_named: list[str] = []
    for entry in named_rules.values():
        when_flag = entry.get("when")
        if when_flag is None:
            # Always active
            active_named.append(str(entry.get("rule", "")))
        elif flags.get(when_flag, False):
            # Flag is truthy → active
            active_named.append(str(entry.get("rule", "")))
        # else: filtered out

    # ----------- Assemble in precedence order (highest first) -----------
    merged: list[str] = []
    merged.extend(injected)     # source 1a
    merged.extend(extra)        # source 1b
    merged.extend(step_rules)   # source 2
    merged.extend(phase_rules)  # source 3
    merged.extend(active_named) # sources 4+5

    return merged


def _build_step_block(
    step_id: str,
    phase_def: dict[str, Any],
    schema: dict[str, Any],
    project: dict[str, Any],
    flags: dict[str, Any],
    repo_name: str,
    state_yaml_path: str,
) -> dict[str, Any]:
    """
    Build the per-step node block for state.yaml workflow_plan (ORC-63).

    Emits: id, status, depends_on (when authored), agent, goal, inputs,
    outputs, rules, repeat_until (when set). The implicit chain `depends_on`
    is left absent here and applied by the topo-sort/promotion step in the
    caller. verify is attached at the phase level, not per node.
    """
    step_entry = _step_entry_for_id(phase_def, step_id) or {}
    contract_raw = _load_step_contract_raw(step_id, state_yaml_path)

    # Defaults for missing contracts
    agent = None
    inputs: list[str] = []
    outputs: list[str] = []
    if contract_raw is not None:
        agent = contract_raw.get("agent")
        raw_inputs = contract_raw.get("inputs") or []
        raw_outputs = contract_raw.get("outputs") or []
        inputs = [str(x) for x in raw_inputs]
        outputs = [str(x) for x in raw_outputs]

    rules = _merge_rules(step_entry, contract_raw, phase_def, schema, project, flags, repo_name)
    goal = phase_def.get("goal", "")

    # Build node with explicit key order (id/status first, then alphabetical).
    block: dict[str, Any] = {
        "id": step_id,
        "status": "pending",
        "agent": agent,
        "goal": goal,
        "inputs": inputs,
        "outputs": outputs,
        "rules": rules,
    }

    # Authored depends_on from the dict-form schema step entry (ORC-63).
    authored = step_entry.get("depends_on")
    if authored:
        block["depends_on"] = [str(d) for d in authored]

    # repeat_until: from schema step entry OR contract, step entry takes precedence
    repeat_until = step_entry.get("repeat_until") or (contract_raw.get("repeat_until") if contract_raw else None)
    if repeat_until:
        block["repeat_until"] = repeat_until

    # Statechart routing edges: on_success / on_failure / max_retries.
    # Values are step ids, or the keyword "halt". Absent = default behavior.
    for edge_key in ("on_success", "on_failure", "max_retries"):
        val = step_entry.get(edge_key)
        if val is not None:
            block[edge_key] = val

    return block


# Legacy name retained for design.md / task verify imports (ORC-77).
_build_node_for_step = _build_step_block


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

    # Resolve effective depends_on for each node (authored or implicit chain).
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
        # Write the effective edges back onto the node.
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
    """
    Write obj as YAML with stable, diffable output.

    sort_keys=False preserves insertion order. Dict keys at the step level
    are pre-sorted by the caller (_build_step_block uses alphabetical insertion).
    """
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
    state = _parser.load_state(state_yaml_path)
    schema_name = state.raw.get("schema", "")
    flags: dict[str, Any] = state.raw.get("flags") or {}
    slug = state.raw.get("slug", state.change_id)

    schema = _load_schema(schema_name)
    project = _load_project(state.repo_root)
    repo_name = (project.get("project") or {}).get("name", "") or slug

    # Resolve schema phases
    resolved_phases = _resolve_phases(schema)

    # Build the promoted workflow_plan in a fresh dict, preserving phase order.
    # Topo-sort each phase BEFORE mutating anything written back, so a cycle
    # aborts with state.yaml untouched.
    promoted: dict[str, Any] = {}
    for phase_name, phase_plan in state.raw.get("workflow_plan", {}).items():
        if isinstance(phase_plan, dict):
            # Idempotent: a re-run sees the promoted `nodes` shape — re-derive
            # the step id order from it. Otherwise read the seed `active` list.
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
            # Keep the phase block unchanged so we don't silently drop it.
            promoted[phase_name] = phase_plan
            continue

        nodes: list[dict[str, Any]] = []
        for step_id in active_step_ids:
            block = _build_step_block(
                step_id=step_id,
                phase_def=phase_def,
                schema=schema,
                project=project,
                flags=flags,
                repo_name=repo_name,
                state_yaml_path=state_yaml_path,
            )
            nodes.append(block)

        filtered_ids = {
            (f.get("id") if isinstance(f, dict) else str(f))
            for f in filtered
        }
        # Topo-sort: raises ValueError on a cycle or unknown-id edge BEFORE
        # any state.yaml write. Mutates each node's effective depends_on.
        _topo_sort(nodes, filtered_ids)

        # Resolve the phase verify block (workflow_plan override, else schema).
        verify_block = phase_plan.get("verify") if isinstance(phase_plan, dict) else None
        if verify_block is None:
            base_verify = phase_def.get("verify")
            verify_when = phase_def.get("verify_when", {})
            if base_verify is not None:
                effective_verify = dict(base_verify)
                for flag_name, override in verify_when.items():
                    if flags.get(flag_name, False):
                        effective_verify.update(override)
                verify_block = effective_verify

        phase_block: dict[str, Any] = {"nodes": nodes, "filtered": filtered}
        if verify_block:
            phase_block["verify"] = verify_block
        promoted[phase_name] = phase_block

    # All phases topo-sorted clean — now rewrite state.yaml in place.
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
