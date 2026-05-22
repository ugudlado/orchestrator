"""
Shared DAG-walk and node-status mutation for the orchestrator engine (ORC-63).

This module is the *single* source of node readiness and the *single*
`node.status` mutator. Both `dispatch.py` (→ `in_progress`) and `record.py`
(→ `completed` / `skipped`) import it so the two status writers and the
next-node computation cannot drift.

A plan node is an entry in `workflow_plan[phase].nodes`:
  {id, depends_on?, status, agent, goal, inputs, outputs, rules, repeat_until?}

`depends_on` absent ⇒ an implicit chain edge on the declaration-order
predecessor (the first node of a phase has no implicit edge).

A node is *ready* when it is not `completed` and every entry in its effective
`depends_on` is `completed`. For a `repeat_until` node, a dependent treats it
as a completed dependency only when its status is `completed` AND its
`repeat_until` predicate evaluates True (design.md OQ-5).
"""
from __future__ import annotations

from typing import Any

from orchestrator_next.parser import State, phase_nodes


def _node_id(node: dict[str, Any]) -> str:
    return str(node.get("id", ""))


def effective_depends_on(nodes: list[dict], node_id: str) -> list[str]:
    """Return the effective dependency ids for a node.

    Authored `depends_on` is honored verbatim. Absent ⇒ the implicit single
    chain edge on the declaration-order predecessor. The first node ⇒ `[]`.
    """
    for idx, node in enumerate(nodes):
        if _node_id(node) == node_id:
            authored = node.get("depends_on")
            if authored is not None:
                return [str(d) for d in authored]
            if idx == 0:
                return []
            return [_node_id(nodes[idx - 1])]
    return []


def _repeat_predicate_satisfied(state: State, node: dict[str, Any]) -> bool:
    """Return True when a node's repeat_until predicate (if any) is True.

    A node with no `repeat_until` always returns True. The predicate set lives
    in `record.py`; it is imported lazily here to avoid a module-load import
    cycle (record.py imports this module).
    """
    repeat_until = node.get("repeat_until")
    if not repeat_until:
        return True
    from orchestrator_next.record import REPEAT_PREDICATES  # lazy: avoid cycle
    predicate = REPEAT_PREDICATES.get(repeat_until)
    if predicate is None:
        # Unknown predicate — treat as absent (matches dispatch/record behavior).
        return True
    return bool(predicate(state.raw))


def _uses_legacy_active_plan(state: State) -> bool:
    """True when the current phase uses pre-ORC-63 `active:[ids]` without `nodes:`."""
    phase_plan = state.workflow_plan.get(state.phase, {})
    if not isinstance(phase_plan, dict):
        return False
    return phase_plan.get("nodes") is None and phase_plan.get("active") is not None


def _step_completed_in_history(state: State, node_id: str) -> bool:
    """Return True when step_history has a terminal completed entry for the node."""
    for entry in reversed(state.step_history):
        if entry.phase != state.phase or entry.step_id != node_id:
            continue
        return entry.status == "completed"
    return False


def _effective_node_status(state: State, node: dict[str, Any]) -> str:
    """Node status with legacy-plan completion inferred from step_history."""
    status = node.get("status")
    if status == "completed":
        return "completed"
    if _uses_legacy_active_plan(state) and _step_completed_in_history(state, _node_id(node)):
        return "completed"
    return str(status or "pending")


def is_node_ready(state: State, node_id: str) -> bool:
    """Return True iff `node_id` is not completed and every effective
    dependency is completed (a repeat_until dep also needs its predicate True).
    """
    nodes = phase_nodes(state, state.phase)
    by_id = {_node_id(n): n for n in nodes}
    node = by_id.get(node_id)
    if node is None:
        return False
    if _effective_node_status(state, node) == "completed":
        return False
    for dep_id in effective_depends_on(nodes, node_id):
        dep = by_id.get(dep_id)
        if dep is None:
            return False
        if _effective_node_status(state, dep) != "completed":
            return False
        if not _repeat_predicate_satisfied(state, dep):
            return False
    return True


def repeat_until_redispatch(state: State, state_yaml_path: str) -> str | None:
    """Return a step_id to re-run when it is completed but its repeat_until predicate is False.

    Covers both promoted `nodes:` plans (repeat_until on the node) and legacy
    `active:[ids]` plans (repeat_until on the step contract only).
    """
    from orchestrator_next.parser import load_contract_for_step, ContractError
    from orchestrator_next.record import REPEAT_PREDICATES

    for node in phase_nodes(state, state.phase):
        node_id = _node_id(node)
        if _effective_node_status(state, node) != "completed":
            continue
        repeat_until = node.get("repeat_until")
        if not repeat_until:
            try:
                contract = load_contract_for_step(node_id, state_yaml_path)
                repeat_until = contract.repeat_until
            except (FileNotFoundError, ContractError):
                repeat_until = None
        if not repeat_until:
            continue
        predicate = REPEAT_PREDICATES.get(repeat_until)
        if predicate is None:
            continue
        if not predicate(state.raw):
            return node_id
    return None


def ready_nodes(state: State) -> list[str]:
    """Return every ready (not-completed, deps satisfied) node id in
    declaration order for the current phase."""
    nodes = phase_nodes(state, state.phase)
    return [_node_id(n) for n in nodes if is_node_ready(state, _node_id(n))]


def next_ready_node(state: State) -> str | None:
    """Return the first ready node id in declaration order, or None when the
    phase has no ready node (complete)."""
    ready = ready_nodes(state)
    return ready[0] if ready else None


def mark_node_status(
    state_raw: dict[str, Any], phase: str, node_id: str, status: str
) -> None:
    """Set `status` on the named node in `state_raw.workflow_plan[phase].nodes`.

    The single node-status mutator. No-op when the phase or node is absent
    (a legacy `active:[ids]` block has no node dicts to mutate).
    """
    phase_plan = (state_raw.get("workflow_plan") or {}).get(phase)
    if not isinstance(phase_plan, dict):
        return
    nodes = phase_plan.get("nodes")
    if not isinstance(nodes, list):
        return
    for node in nodes:
        if isinstance(node, dict) and str(node.get("id", "")) == node_id:
            node["status"] = status
            return
