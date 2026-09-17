"""
Shared DAG-walk and node-status mutation for the orchestrator engine.

This module is the *single* source of node readiness and the *single*
`node.status` mutator. Both `dispatch.py` (→ `in_progress`) and `record.py`
(→ `completed` / `skipped`) import it so the two status writers and the
next-node computation cannot drift.

A plan node is an entry in `workflow_plan[phase].nodes`:
  {id, depends_on?, status}

`depends_on` is always explicit — generate_plan writes it at init time.
Absent means no dependencies (the node is unconditionally ready once
its predecessors complete, or immediately for the first node).

A node is *ready* when it is neither `completed` nor terminal-but-not-done
(`abandoned` / `failed`), and every entry in its `depends_on` is `completed`.
"""
from __future__ import annotations

from typing import Any

from orchestrator_next.parser import State, phase_nodes


#: Node statuses that end the node without completing it. A node in one of
#: these is never ready (no re-dispatch) and never satisfies a dependency
#: (dependents stay blocked). `record._apply_routing` is what writes them.
_TERMINAL_NOT_DONE = frozenset({"abandoned", "failed"})


def _node_id(node: dict[str, Any]) -> str:
    return str(node.get("id", ""))


def find_node(nodes: list[dict], node_id: str) -> dict | None:
    """Return the node dict whose id matches node_id, or None."""
    for node in nodes:
        if _node_id(node) == node_id:
            return node
    return None


def effective_depends_on(nodes: list[dict], node_id: str) -> list[str]:
    """Return the dependency ids for a node.

    generate_plan always writes explicit depends_on into state.yaml, so this
    just reads the authored list. Absent means no dependencies.
    """
    authored = (find_node(nodes, node_id) or {}).get("depends_on")
    return [str(d) for d in authored] if authored else []


def _step_completed_in_history(state: State, node_id: str) -> bool:
    """Return True when step_history has a terminal completed entry for the node."""
    for entry in state.step_history:
        if entry.phase != state.phase or entry.step_id != node_id:
            continue
        if entry.status in ("completed", "recovered"):
            return True
    return False


def _effective_node_status(state: State, node: dict[str, Any]) -> str:
    """Node status with step_history completion inference.

    Explicit ``completed`` always wins.
    ``"reset"`` (written by on_failure routing via mark_node_status) wins over
    step_history — it means "re-open this node even though history says done".
    All other statuses (``"pending"``, absent, etc.) are overridden by
    step_history when a completed/recovered entry exists.
    """
    status = node.get("status")
    if status == "completed":
        return "completed"
    if status == "reset":
        return "pending"
    if status in _TERMINAL_NOT_DONE:
        # The step ran and gave up (`abandoned`) or failed outright. It is not
        # completed, so dependents stay blocked; it is not pending either, so
        # it is never re-dispatched. Only a human (or an explicit reset) moves
        # the run past it.
        return status
    if _step_completed_in_history(state, _node_id(node)):
        return "completed"
    return str(status or "pending")


def _is_node_ready(
    state: State, node_id: str, nodes: list[dict], by_id: dict
) -> bool:
    """Return True iff `node_id` is not completed and every effective dependency is completed.

    Accepts pre-built `nodes` and `by_id` so callers that iterate all nodes
    avoid rebuilding the list and dict on every call.
    """
    node = by_id.get(node_id)
    if node is None:
        return False
    own = _effective_node_status(state, node)
    if own == "completed" or own in _TERMINAL_NOT_DONE:
        return False
    for dep_id in effective_depends_on(nodes, node_id):
        dep = by_id.get(dep_id)
        if dep is None:
            return False
        if _effective_node_status(state, dep) != "completed":
            return False
    return True


def is_node_ready(state: State, node_id: str) -> bool:
    """Return True iff `node_id` is ready. Public API; builds node index internally."""
    nodes = phase_nodes(state, state.phase)
    by_id = {_node_id(n): n for n in nodes}
    return _is_node_ready(state, node_id, nodes, by_id)



def ready_nodes(state: State, *, exclude_claimed: bool = False) -> list[str]:
    """Return every ready (not-completed, deps satisfied) node id in
    declaration order for the current phase.

    ``exclude_claimed`` drops nodes already marked ``in_progress``. Serial
    dispatch does NOT want this: a crashed step leaves an ``in_progress`` node
    that must be re-dispatched, and ``dispatch()`` handles that through its
    resume path. Parallel dispatch DOES want it, because there an
    ``in_progress`` node is a claim held by another worker and handing it out
    twice would run the same step concurrently with itself.
    """
    nodes = phase_nodes(state, state.phase)
    by_id = {_node_id(n): n for n in nodes}
    out = [_node_id(n) for n in nodes if _is_node_ready(state, _node_id(n), nodes, by_id)]
    if exclude_claimed:
        out = [nid for nid in out
               if str((by_id.get(nid) or {}).get("status") or "") != "in_progress"]
    return out


def abandoned_nodes(state: State) -> list[str]:
    """Every node in the current phase that ended `abandoned`, in plan order.

    A non-empty result alongside an empty ready set means the phase is stuck,
    not finished: the abandoned node never wrote its outputs, so its
    dependents can never become ready.
    """
    return [
        _node_id(n)
        for n in phase_nodes(state, state.phase)
        if n.get("status") == "abandoned"
    ]


def next_ready_node(state: State) -> str | None:
    """Return the first ready node id in declaration order, or None when the
    phase has no ready node (complete)."""
    ready = ready_nodes(state)
    return ready[0] if ready else None


def mark_node_status(
    state_raw: dict[str, Any], phase: str, node_id: str, status: str
) -> None:
    """Set `status` on the named node in `state_raw.workflow_plan[phase].nodes`.

    The single node-status mutator. No-op when the phase or node is absent.
    """
    phase_plan = (state_raw.get("workflow_plan") or {}).get(phase)
    if not isinstance(phase_plan, dict):
        return
    nodes = phase_plan.get("nodes")
    if not isinstance(nodes, list):
        return
    node = find_node(nodes, node_id)
    if node is not None:
        node["status"] = status
