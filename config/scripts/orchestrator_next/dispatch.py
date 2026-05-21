"""
Pure dispatcher: State → (action_dict, exit_code).

Protocol (ORC-45 two-path dispatch):
  exit 0 + JSON with agent key  → driver spawns Agent tool
  exit 0 + no JSON              → inline script ran and recorded; driver loops
  exit 1                        → workflow complete; driver reads state.yaml
  exit 2                        → step blocked; driver reads state.yaml
  exit 3                        → ContractDispatchError (missing agent: and run:)

No action field. No signal field. No verify_phase.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

from orchestrator_next.parser import (
    ContractError,
    ContractDispatchError,
    State,
    StepContract,
    StepHistoryEntry,
    load_contract_for_step,
    phase_nodes,
)
from orchestrator_next import resolver
from orchestrator_next import readiness

# Blocking statuses: caller cannot proceed
_BLOCKING_STATUSES = frozenset({"escalate_to_architect", "blocked"})


def _compute_attempt(step_history: list[StepHistoryEntry], phase: str, step_id: str) -> int:
    """
    Compute the next attempt number for a (phase, step_id) pair.

    Scans step_history for all entries matching phase+step_id, finds the
    maximum attempt number, and returns max + 1 (default 1 when none exist).
    """
    attempts = [
        e.attempt
        for e in step_history
        if e.phase == phase and e.step_id == step_id and e.attempt is not None
    ]
    return (max(attempts) + 1) if attempts else 1


def _build_env(
    state: State,
    step_id: str,
    attempt: int,
) -> dict[str, str]:
    """Build the ORCHESTRATOR_* env block for the action response."""
    return {
        "ORCHESTRATOR_CHANGE_ID": state.change_id,
        "ORCHESTRATOR_PHASE": state.phase,
        "ORCHESTRATOR_STEP_ID": step_id,
        "ORCHESTRATOR_ATTEMPT": str(attempt),
        "ORCHESTRATOR_WORKFLOW_DIR": state.workflow_dir,
        "ORCHESTRATOR_REPO_ROOT": state.repo_root,
        "ORCHESTRATOR_WORKTREE_ARTIFACT_DIR": state.worktree_artifact_dir,
    }


def _get_last_entry(step_history: list[StepHistoryEntry]) -> StepHistoryEntry | None:
    return step_history[-1] if step_history else None


def _resolve_inputs(
    state: State, contract: StepContract
) -> tuple[dict[str, Any], list[str]]:
    """Resolve a contract's declared ``inputs:`` against prior step outputs.

    For each name in ``contract.inputs``, walk ``state.step_history`` in
    reverse for a terminal ``completed`` entry whose
    ``evidence.outputs.<name>`` is set; fall back to ``state.raw`` top-level
    (bootstrap values like ``slug`` / ``repo_root`` / ``change_id``).
    Returns ``(resolved, missing)``. Missing names are not an error here —
    the caller decides. Contracts with empty ``inputs:`` return ``({}, [])``.
    """
    if not contract.inputs:
        return {}, []

    resolved: dict[str, Any] = {}
    missing: list[str] = []

    for name in contract.inputs:
        found = False
        for entry in reversed(state.step_history):
            if entry.status != "completed":
                continue
            outputs = (entry.raw.get("evidence") or {}).get("outputs") or {}
            if name in outputs:
                resolved[name] = outputs[name]
                found = True
                break
        if not found and name in state.raw:
            resolved[name] = state.raw[name]
            found = True
        if not found:
            missing.append(name)

    return resolved, missing


def _resolve_allowed_tools(contract: StepContract) -> list[str]:
    """
    Compute the resolved tool list for a step contract.

    Logic (from design.md § Low-Level Design pseudocode):
      - agent == "inline" + allowed_tools set → warn, return []
      - agent == "inline" + no allowed_tools → return []
      - role unresolvable (None) + allowed_tools set → warn, return []
      - role unresolvable (None) + no allowed_tools → return []
      - allowed_tools non-empty + widens role → ContractError
      - allowed_tools non-empty, no widening → sorted intersection
      - allowed_tools empty (absent/null/[]) → sorted full role list
    """
    # For inline-script steps (no agent or agent="inline"), tools don't apply
    if not contract.agent or contract.agent == "inline":
        if contract.allowed_tools:
            print(
                f"WARNING: allowed_tools on inline step {contract.id!r} ignored",
                file=sys.stderr,
            )
        return []

    role_tools = resolver.load_agent_tools(contract.agent)

    if role_tools is None:
        if contract.allowed_tools:
            print(
                f"WARNING: cannot resolve agent {contract.agent!r} tools; "
                f"allowed_tools on step {contract.id!r} not enforced",
                file=sys.stderr,
            )
        return []

    if contract.allowed_tools:
        declared = set(contract.allowed_tools)
        illegal = declared - role_tools
        if illegal:
            raise ContractError(
                f"allowed_tools on step {contract.id!r} declares "
                f"{sorted(illegal)!r} not in agent {contract.agent!r} tools"
            )
        return sorted(declared & role_tools)

    # Empty allowed_tools (absent, null, or []) → full role list (backward-compat)
    return sorted(role_tools)


def _node_step_context(state: State, step_id: str) -> dict[str, Any]:
    """Return the plan node dict for (current phase, step_id) as step_context.

    ORC-63: the per-step data formerly held in plan.yaml now lives on the node
    in `state.workflow_plan[phase].nodes`. A legacy `active:[ids]` block yields
    a synthesized bare node (back-compat read path, AC-11).
    """
    for node in phase_nodes(state, state.phase):
        if str(node.get("id", "")) == step_id:
            return dict(node)
    return {"id": step_id}


def _persist_node_status(state_yaml_path: str, phase: str, step_id: str, status: str) -> None:
    """Mark a node's status in state.yaml on disk via readiness.mark_node_status.

    A narrow state.yaml writer for the dispatch-time `in_progress` transition.
    No-op for a legacy `active:[ids]` block (no node dicts to mutate).
    """
    path = Path(state_yaml_path)
    try:
        with open(path, "rb") as f:
            pre_bytes = f.read()
        state_raw = yaml.safe_load(pre_bytes.decode("utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return
    readiness.mark_node_status(state_raw, phase, step_id, status)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(state_raw, f, sort_keys=False, default_flow_style=False, allow_unicode=True)
    # Post-write corruption guard: restore pre-write bytes if unparseable.
    try:
        with open(path, "r", encoding="utf-8") as f:
            yaml.safe_load(f)
    except yaml.YAMLError:
        with open(path, "wb") as f:
            f.write(pre_bytes)


def _check_required_inputs(
    state: State, contract: StepContract, step_id: str
) -> int | None:
    """Return exit code 2 if a required input is unresolvable, else None.

    AC-4: a required input is *missing* iff it is not the key of any prior
    `completed` step's `evidence.outputs` and not a top-level `state.raw` key.
    Inputs named in `contract.optional_inputs` never block.
    """
    _resolved, missing = _resolve_inputs(state, contract)
    optional = set(contract.optional_inputs)
    required_missing = [m for m in missing if m not in optional]
    if required_missing:
        print(
            f"ERROR: step {step_id!r} blocked — required input(s) "
            f"{required_missing!r} unresolvable: no prior completed step "
            f"produced them under evidence.outputs and they are absent from "
            f"state.raw. The upstream producer has not completed.",
            file=sys.stderr,
        )
        return 2
    return None


def dispatch(state: State, state_yaml_path: str) -> tuple[dict[str, Any], int]:
    """
    DAG-walk dispatcher: State → (action_dict, exit_code).

    ORC-63: selects the next step via `readiness.next_ready_node(state)` over
    `workflow_plan[phase].nodes` — no plan.yaml. Required-input prereqs are a
    hard block (exit 2). On a fresh selection the chosen node is marked
    `in_progress` in state.yaml.
    """
    last = _get_last_entry(state.step_history)

    # --- Check: last entry is a blocking status → exit 2, no JSON (ORC-45)
    if last is not None and last.phase == state.phase and last.status in _BLOCKING_STATUSES:
        return {}, 2

    # --- Check: last entry is in_progress → resume (post-reconcile, this is the DB truth)
    if (
        last is not None
        and last.phase == state.phase
        and last.status == "in_progress"
        and last.ended_at is None
    ):
        step_id = last.step_id
        # Resume: keep the ORIGINAL attempt. DO NOT call _compute_attempt here —
        # it returns max+1 (retry semantics). Resume semantics require attempt unchanged.
        attempt = last.attempt if last.attempt is not None else 1
        try:
            contract = load_contract_for_step(step_id, state_yaml_path)
        except FileNotFoundError:
            # Fall back to inline-only contract with minimal data
            contract = StepContract(
                id=step_id,
                agent=last.agent or "inline",
                run=None,
                instruction="",
                rules=[],
            )
        inputs_resolved, _missing = _resolve_inputs(state, contract)
        resolved_allowed_tools = _resolve_allowed_tools(contract)
        action = {
            "step_id": step_id,
            "phase": state.phase,
            "attempt": attempt,
            "is_resume": True,
            "started_at": last.started_at,
            "agent": contract.agent,
            "instruction": contract.instruction,
            "rules": contract.rules,
            "inputs": inputs_resolved,
            "expected_outputs": contract.outputs,
            "resolved_allowed_tools": resolved_allowed_tools,
            "env": _build_env(state, step_id, attempt),
            "step_context": _node_step_context(state, step_id),
        }
        return action, 0

    # --- DAG-walk: select the first ready node (declaration-order tiebreak)
    next_step_id = readiness.next_ready_node(state)

    # --- Check: no ready node → exit 1, no JSON (phase complete) (ORC-45)
    if next_step_id is None:
        # Warn if this phase is not the last in workflow_plan (driver must advance phase)
        plan = (state.workflow_plan or {})
        phase_names = list(plan.keys())
        if len(phase_names) > 1 and state.phase in phase_names:
            current_idx = phase_names.index(state.phase)
            remaining = phase_names[current_idx + 1:]
            if remaining:
                print(
                    f"WARNING: phase '{state.phase}' is complete but "
                    f"workflow_plan has other phases ({', '.join(remaining)}). "
                    f"Driver must advance state.yaml 'phase' field and re-run "
                    f"'orchestrator next' before completing workflow.",
                    file=sys.stderr,
                )
        return {}, 1

    # --- Load contract for the next step
    try:
        contract = load_contract_for_step(next_step_id, state_yaml_path)
    except FileNotFoundError:
        # Fall back to inline-only contract with minimal data when the step
        # contract was deleted after workflow_plan was frozen at pre-dispatch init.
        # Mirrors the resume_step branch above.
        contract = StepContract(
            id=next_step_id,
            agent="inline",
            run=None,
            instruction="",
            rules=[],
        )

    # --- Prerequisite hard block (AC-4): a required input that no prior
    #     completed step produced and that is absent from state.raw → exit 2.
    block_code = _check_required_inputs(state, contract, next_step_id)
    if block_code is not None:
        return {}, block_code

    attempt = _compute_attempt(state.step_history, state.phase, next_step_id)
    env = _build_env(state, next_step_id, attempt)
    inputs_resolved, _missing = _resolve_inputs(state, contract)
    resolved_allowed_tools = _resolve_allowed_tools(contract)
    step_context = _node_step_context(state, next_step_id)

    # ORC-45 two-path dispatch: agent: → spawn; run: → execute inline; else → error.
    if contract.agent:
        action = {
            "step_id": next_step_id,
            "phase": state.phase,
            "attempt": attempt,
            "agent": contract.agent,
            "instruction": contract.instruction,
            "rules": contract.rules,
            "inputs": inputs_resolved,
            "expected_outputs": contract.outputs,
            "resolved_allowed_tools": resolved_allowed_tools,
            "env": env,
            "step_context": step_context,
        }
    elif contract.run:
        # Inline script executed synchronously by CLI — no JSON emitted, exit 0
        action = {
            "step_id": next_step_id,
            "phase": state.phase,
            "attempt": attempt,
            "run": contract.run,
            "instruction": contract.instruction,
            "rules": contract.rules,
            "inputs": inputs_resolved,
            "expected_outputs": contract.outputs,
            "resolved_allowed_tools": resolved_allowed_tools,
            "env": env,
            "step_context": step_context,
        }
    else:
        raise ContractDispatchError(
            f"step_contract_missing_run: {next_step_id}"
        )

    # ORC-63: mark the chosen node in_progress in state.yaml (the one
    # status mutator). No-op for a legacy active:[ids] block.
    _persist_node_status(state_yaml_path, state.phase, next_step_id, "in_progress")
    return action, 0


def emit_json(obj: dict[str, Any]) -> str:
    """
    Emit the action dict as deterministic, sorted-keys, indented JSON.

    Always uses sort_keys=True and indent=2 for byte-identical output
    regardless of TTY — satisfies AC-1 (deterministic) and test byte-compare.
    """
    return json.dumps(obj, sort_keys=True, indent=2) + "\n"
