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
)
from orchestrator_next import resolver
from orchestrator_next.record import REPEAT_PREDICATES

# Terminal statuses: these entries do not need retry
_TERMINAL_STATUSES = frozenset({"completed", "failed", "blocked", "escalate_to_architect", "skipped"})
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


def _phase_step_ids(state: State) -> list[str]:
    """Return the active step IDs for the current phase from workflow_plan."""
    phase_plan = state.workflow_plan.get(state.phase, {})
    if isinstance(phase_plan, dict):
        return list(phase_plan.get("active", []))
    return []


def _phase_verify_block(state: State) -> dict[str, Any] | None:
    """Return the verify block for the current phase, or None if absent."""
    phase_plan = state.workflow_plan.get(state.phase, {})
    if isinstance(phase_plan, dict):
        verify = phase_plan.get("verify")
        if verify:
            return verify
    return None


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


def _phase_history(step_history: list[StepHistoryEntry], phase: str) -> list[StepHistoryEntry]:
    return [e for e in step_history if e.phase == phase]


def _step_has_terminal_entry(
    step_history: list[StepHistoryEntry], phase: str, step_id: str
) -> bool:
    """Return True if any entry for (phase, step_id) has a terminal status."""
    return any(
        e.phase == phase and e.step_id == step_id and e.status in _TERMINAL_STATUSES
        for e in step_history
    )


def _find_completed_step(
    step_history: list[StepHistoryEntry], phase: str, step_id: str
) -> bool:
    """Return True if any completed entry exists for (phase, step_id)."""
    return any(
        e.phase == phase and e.step_id == step_id and e.status == "completed"
        for e in step_history
    )


def _phase_verify_evaluated(step_history: list[StepHistoryEntry], phase: str) -> bool:
    """
    Return True if a run-phase-review step with a terminal status exists for this phase.

    The run-phase-review entry signals that the caller ran the verify block.
    """
    return any(
        e.phase == phase and e.step_id == "run-phase-review" and e.status in _TERMINAL_STATUSES
        for e in step_history
    )


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


def _load_plan(state_yaml_path: str) -> dict[str, Any]:
    """
    Load plan.yaml from the directory containing state.yaml.

    Exits 3 with a clear stderr message if plan.yaml is missing.
    """
    plan_path = Path(state_yaml_path).parent / "plan.yaml"
    if not plan_path.exists():
        print(
            f"ERROR: plan.yaml not found at {plan_path}. "
            "Run 'python -m orchestrator_next.generate_plan <state_yaml_path>' to generate it.",
            file=sys.stderr,
        )
        sys.exit(3)
    with open(plan_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _find_step_in_plan(plan: dict[str, Any], phase: str, step_id: str) -> dict[str, Any]:
    """
    Return the step block for (phase, step_id) from plan.yaml.

    Exits 3 with a clear stderr message if not found.
    """
    for phase_block in plan.get("phases", []):
        if phase_block.get("name") == phase:
            for step in phase_block.get("steps", []):
                if step.get("id") == step_id:
                    return step
    print(
        f"ERROR: step_context missing for {phase}/{step_id} in plan.yaml. "
        "Re-generate plan.yaml if the workflow_plan changed.",
        file=sys.stderr,
    )
    sys.exit(3)


def dispatch(state: State, state_yaml_path: str) -> tuple[dict[str, Any], int]:
    """
    Pure function: State → (action_dict, exit_code).

    Does not mutate state. Does not write to state.yaml or DuckDB
    (DuckDB upsert is wired in by main — T-4 scope, not here).
    """
    step_ids = _phase_step_ids(state)
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
        }
        plan = _load_plan(state_yaml_path)
        action["step_context"] = _find_step_in_plan(plan, state.phase, step_id)
        return action, 0

    # --- Determine the next pending step in this phase
    next_step_id: str | None = None
    for sid in step_ids:
        if not _find_completed_step(state.step_history, state.phase, sid):
            next_step_id = sid
            break
        # Step is marked completed — but if its contract declares repeat_until,
        # evaluate the predicate. If False, re-emit this step (don't advance).
        try:
            sid_contract = load_contract_for_step(sid, state_yaml_path)
        except (FileNotFoundError, ContractError):
            sid_contract = None
        if sid_contract is not None and sid_contract.repeat_until:
            predicate = REPEAT_PREDICATES.get(sid_contract.repeat_until)
            if predicate is not None and not predicate(state.raw):
                next_step_id = sid
                break

    # --- Check: all phase steps completed → exit 1, no JSON (ORC-45)
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

        # All phases complete — exit 1, no JSON
        return {}, 1

    # --- Load contract for the next step
    try:
        contract = load_contract_for_step(next_step_id, state_yaml_path)
    except FileNotFoundError:
        # Fall back to inline-only contract with minimal data when the step
        # contract was deleted after workflow_plan was frozen at workflow-init.
        # Mirrors the resume_step branch above.
        contract = StepContract(
            id=next_step_id,
            agent="inline",
            run=None,
            instruction="",
            rules=[],
        )
    attempt = _compute_attempt(state.step_history, state.phase, next_step_id)
    env = _build_env(state, next_step_id, attempt)
    inputs_resolved, _missing = _resolve_inputs(state, contract)
    resolved_allowed_tools = _resolve_allowed_tools(contract)

    # M1 note: missing inputs are NOT an error yet. Strict validation that
    # blocks on missing inputs is M2's exit criterion — M1 only threads
    # values through when available. This keeps M1 backward-compatible
    # with contracts that declare `inputs:` but whose producers haven't
    # yet been migrated to emit them under `evidence.outputs.<name>`.

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
        }
        plan_data = _load_plan(state_yaml_path)
        action["step_context"] = _find_step_in_plan(plan_data, state.phase, next_step_id)
        return action, 0
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
        }
        plan_data = _load_plan(state_yaml_path)
        action["step_context"] = _find_step_in_plan(plan_data, state.phase, next_step_id)
        return action, 0
    else:
        raise ContractDispatchError(
            f"step_contract_missing_run: {next_step_id}"
        )


def emit_json(obj: dict[str, Any]) -> str:
    """
    Emit the action dict as deterministic, sorted-keys, indented JSON.

    Always uses sort_keys=True and indent=2 for byte-identical output
    regardless of TTY — satisfies AC-1 (deterministic) and test byte-compare.
    """
    return json.dumps(obj, sort_keys=True, indent=2) + "\n"
