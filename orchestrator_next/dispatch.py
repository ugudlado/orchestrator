"""
Pure dispatcher: State → (action_dict, exit_code).

Two-path dispatch protocol:
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

from orchestrator_next import readiness
from orchestrator_next.step_env import build_dispatch_env as _build_dispatch_env
from orchestrator_next.parser import (
    AgentStepContract,
    ScriptStepContract,
    State,
    StepContract,
    StepHistoryEntry,
    load_contract_for_step,
    phase_nodes,
    safe_write_yaml as _safe_write_yaml,
)
from orchestrator_next.step_runner import step_directory as _step_directory


def _step_in_plan(state, phase: str, step_id: str) -> bool:
    """True when step_id is a node in workflow_plan for this phase."""
    return any(str(n.get("id", "")) == step_id for n in phase_nodes(state, phase))


class ContractDispatchError(RuntimeError):
    """Missing step contract or agent file on disk; run /doctor to diagnose."""


# Blocking statuses: caller cannot proceed
_BLOCKING_STATUSES = frozenset({"escalate_to_architect", "blocked"})
_DEFAULT_MAX_SPAWN_FAILURES = 3


def _compute_attempt(step_history: list[StepHistoryEntry], phase: str, step_id: str) -> int:
    """Return the next attempt number for a (phase, step_id) pair.

    Scans all history entries including in_progress. record.py has a parallel
    version that excludes in_progress entries (different semantics for recording).
    """
    attempts = [
        e.attempt
        for e in step_history
        if e.phase == phase and e.step_id == step_id and e.attempt is not None
    ]
    return (max(attempts) + 1) if attempts else 1


def _is_spawn_failure(entry: StepHistoryEntry) -> bool:
    """True when the entry is a pre-agent spawn failure (model=none, zero tokens)."""
    if entry.status != "failed":
        return False
    usage = entry.usage if isinstance(entry.usage, dict) else {}
    model = usage.get("model")
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    return model == "none" and input_tokens == 0 and output_tokens == 0


def _consecutive_spawn_failures(
    step_history: list[StepHistoryEntry], phase: str, step_id: str
) -> int:
    """Count trailing spawn failures for (phase, step_id) in step_history."""
    count = 0
    for entry in reversed(step_history):
        if entry.phase != phase or entry.step_id != step_id:
            continue
        if _is_spawn_failure(entry):
            count += 1
            continue
        break
    return count


def _get_last_entry(step_history: list[StepHistoryEntry]) -> StepHistoryEntry | None:
    return step_history[-1] if step_history else None




def _node_step_context(state: State, step_id: str) -> dict[str, Any]:
    """Return the plan node dict for (current phase, step_id) as step_context.

    Per-step data lives on the node in `state.workflow_plan[phase].nodes`.
    A legacy `active:[ids]` block yields a synthesized bare node (back-compat read path).
    """
    node = readiness.find_node(phase_nodes(state, state.phase), step_id)
    return dict(node) if node is not None else {"id": step_id}



def _persist_node_status(
    state_yaml_path: str,
    phase: str,
    step_id: str,
    status: str,
    state_raw: dict | None = None,
) -> None:
    """Mark a node's status in state.yaml on disk via readiness.mark_node_status.

    A narrow state.yaml writer for the dispatch-time `in_progress` transition.
    No-op for a legacy `active:[ids]` block (no node dicts to mutate).
    """
    path = Path(state_yaml_path)
    try:
        with open(path, "rb") as f:
            pre_bytes = f.read()
        if state_raw is None:
            state_raw = yaml.safe_load(pre_bytes.decode("utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return
    readiness.mark_node_status(state_raw, phase, step_id, status)
    try:
        _safe_write_yaml(path, state_raw, pre_bytes)
    except yaml.YAMLError:
        pass  # pre_bytes already restored by safe_write_yaml


def _build_action_base(
    contract: StepContract,
    step_id: str,
    phase: str,
    attempt: int,
    state: State,
    state_yaml_path: str,
) -> dict[str, Any]:
    """Build the base keys shared by both resume and fresh-dispatch action dicts.

    Resume path adds: is_resume, started_at, agent, learnings.
    Fresh path adds: pre, post, then agent+learnings or run+step_contract_dir.
    """
    return {
        "step_id": step_id,
        "phase": phase,
        "attempt": attempt,
        "instruction": contract.instruction if isinstance(contract, AgentStepContract) else "",
        "env": _build_dispatch_env(state, step_id, attempt, state_yaml_path),
        "step_context": _node_step_context(state, step_id),
    }


def _warn_if_more_phases_remain(state: State) -> None:
    """Emit a stderr warning when the current phase is complete but the plan has more phases.

    The driver must manually advance state.yaml's `phase` field before re-running
    `orchestrator next`. Without this warning, phase-complete looks identical to
    workflow-complete from the outside.
    """
    plan = state.workflow_plan or {}
    phase_names = list(plan.keys())
    if len(phase_names) > 1 and state.phase in phase_names:
        remaining = phase_names[phase_names.index(state.phase) + 1:]
        if remaining:
            print(
                f"WARNING: phase '{state.phase}' is complete but "
                f"workflow_plan has other phases ({', '.join(remaining)}). "
                f"Driver must advance state.yaml 'phase' field and re-run "
                f"'orchestrator next' before completing workflow.",
                file=sys.stderr,
            )


def _resolve_step_contract_dir(step_id: str, contract: ScriptStepContract) -> str:
    """Return the contract directory path for a script contract, or empty string."""
    orch_home = os.environ.get("ORCHESTRATOR_HOME", "")
    if orch_home:
        return str(_step_directory(step_id, contract, orch_home))
    if os.path.isabs(contract.run):
        return os.path.dirname(contract.run)
    return ""


def _handle_resume(
    state: State, state_yaml_path: str, last: StepHistoryEntry
) -> tuple[dict[str, Any], int]:
    """Resume an in-progress step.

    Keeps the ORIGINAL attempt number — do not call _compute_attempt here
    (that returns max+1, which is retry semantics, not resume semantics).
    """
    step_id = last.step_id
    attempt = last.attempt if last.attempt is not None else 1
    try:
        contract = load_contract_for_step(step_id, state_yaml_path, workflow_plan=state.workflow_plan)
    except (FileNotFoundError, ContractDispatchError):
        contract = AgentStepContract(id=step_id, model=last.agent, instruction="")
    action = _build_action_base(
        contract,
        step_id,
        state.phase,
        attempt,
        state,
        state_yaml_path,
    )
    action["is_resume"] = True
    action["started_at"] = last.started_at
    if isinstance(contract, AgentStepContract):
        action["model"] = contract.model
    return action, 0


def _dispatch_fresh(
    state: State, state_yaml_path: str, next_step_id: str
) -> tuple[dict[str, Any], int]:
    """Dispatch a fresh (non-resume) step node."""
    contract = load_contract_for_step(next_step_id, state_yaml_path, workflow_plan=state.workflow_plan)

    spawn_failures = _consecutive_spawn_failures(
        state.step_history, state.phase, next_step_id
    )
    max_spawn_failures = _DEFAULT_MAX_SPAWN_FAILURES
    if spawn_failures >= max_spawn_failures:
        print(
            f"BLOCKED: spawn_failure_cap — {spawn_failures} consecutive zero-token "
            f"failures for {state.phase}/{next_step_id}",
            file=sys.stderr,
        )
        return {"reason": "spawn_failure_cap"}, 2

    attempt = _compute_attempt(state.step_history, state.phase, next_step_id)

    action = _build_action_base(
        contract,
        next_step_id,
        state.phase,
        attempt,
        state,
        state_yaml_path,
    )
    action["pre"] = contract.pre
    action["post"] = contract.post
    if isinstance(contract, AgentStepContract):
        action["model"] = contract.model
    else:
        action["run"] = contract.run
        step_contract_dir = _resolve_step_contract_dir(next_step_id, contract)
        if step_contract_dir:
            action["step_contract_dir"] = step_contract_dir

    # Mark the chosen node in_progress in state.yaml (the one status mutator).
    # No-op for a legacy active:[ids] block.
    _persist_node_status(state_yaml_path, state.phase, next_step_id, "in_progress", state_raw=state.raw)
    return action, 0


def dispatch(state: State, state_yaml_path: str) -> tuple[dict[str, Any], int]:
    """DAG-walk dispatcher: State → (action_dict, exit_code).

    exit 0 + JSON with agent key → driver spawns Agent tool
    exit 0 + no JSON → inline script ran and recorded; driver loops
    exit 1 → workflow complete
    exit 2 → step blocked
    exit 3 → ContractDispatchError
    """
    last = _get_last_entry(state.step_history)

    if last is not None and last.phase == state.phase and last.status in _BLOCKING_STATUSES:
        return {}, 2

    if (
        last is not None
        and last.phase == state.phase
        and last.status == "in_progress"
        and last.ended_at is None
    ):
        if not _step_in_plan(state, state.phase, last.step_id):
            print(
                f"ERROR: refusing to resume step {last.step_id!r} — "
                f"not in workflow_plan[{state.phase!r}].nodes "
                f"(likely ghost from prior schema or stale state.yaml entry).",
                file=sys.stderr,
            )
            return {}, 3
        return _handle_resume(state, state_yaml_path, last)

    next_step_id = readiness.next_ready_node(state)

    if next_step_id is None:
        _warn_if_more_phases_remain(state)
        return {}, 1

    return _dispatch_fresh(state, state_yaml_path, next_step_id)


def emit_json(obj: dict[str, Any]) -> str:
    """
    Emit the action dict as deterministic, sorted-keys, indented JSON.

    Always uses sort_keys=True and indent=2 for byte-identical output
    regardless of TTY — deterministic, byte-identical for test comparison.
    """
    return json.dumps(obj, sort_keys=True, indent=2) + "\n"
