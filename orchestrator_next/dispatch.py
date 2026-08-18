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
import sys
from typing import Any

import yaml

from orchestrator_next import readiness
from orchestrator_next.model_routes import resolve_step_alias
from orchestrator_next.paths import ConfigRootError, config_root
from orchestrator_next.step_env import build_dispatch_env as _build_dispatch_env
from orchestrator_next.parser import (
    AgentStepContract,
    State,
    StepContract,
    StepHistoryEntry,
    compute_attempt,
    load_contract_for_step,
    phase_nodes,
)


def _models_yaml_path() -> str | None:
    """Config-root models.yaml — ORCHESTRATOR_MODELS_CONFIG is applied via
    model_routes layer chain (env_file), not by replacing this path."""
    try:
        return str(config_root() / "models.yaml")
    except ConfigRootError:
        return None


def _resolved_model(step_id: str, contract: AgentStepContract) -> str:
    """Resolve tier alias from models.yaml step_models (required)."""
    alias = resolve_step_alias(step_id, None, _models_yaml_path())
    if not alias:
        raise ContractDispatchError(
            f"step {step_id}: no step_models entry in models.yaml — "
            f"add step_models.{step_id}: <strong|standard|code>"
        )
    return alias


def _step_in_plan(state, phase: str, step_id: str) -> bool:
    """True when step_id is a node in workflow_plan for this phase."""
    return any(str(n.get("id", "")) == step_id for n in phase_nodes(state, phase))


class ContractDispatchError(RuntimeError):
    """Missing step contract or agent file on disk; run /doctor to diagnose."""


# Blocking statuses: caller cannot proceed
_BLOCKING_STATUSES = frozenset({"escalate_to_architect", "blocked"})
_DEFAULT_MAX_SPAWN_FAILURES = 3


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


def _node_step_context(state: State, step_id: str) -> dict[str, Any]:
    """Return the plan node dict for (current phase, step_id) as step_context."""
    node = readiness.find_node(phase_nodes(state, state.phase), step_id)
    return dict(node) if node is not None else {"id": step_id}


def _prompt_dir_map(state: State) -> dict[str, str]:
    """Map step_id → resolved prompt dir for every agent step in the workflow.

    Spans all phases, not just the current one: a step (learn) may need to write
    beside a step from an earlier phase. Steps whose contract is missing or
    malformed are skipped — a partial map must not fail dispatch.
    """
    dirs: dict[str, str] = {}
    for phase in state.workflow_plan:
        for node in phase_nodes(state, phase):
            step_id = str(node.get("id", ""))
            if not step_id or step_id in dirs:
                continue
            try:
                contract = load_contract_for_step(step_id)
            except Exception:
                continue
            if isinstance(contract, AgentStepContract) and contract.prompt_dir:
                dirs[step_id] = contract.prompt_dir
    return dirs



def _persist_node_status(
    state_yaml_path: str,
    phase: str,
    step_id: str,
    state_raw: dict,
) -> None:
    """Claim a node by marking it in_progress in the state store."""
    _claim_nodes(state_yaml_path, phase, [step_id], state_raw=state_raw)


def _claim_nodes(
    state_yaml_path: str,
    phase: str,
    step_ids: list[str],
    state_raw: dict,
) -> bool:
    """Mark every id in `step_ids` in_progress in ONE compare-and-swap write.

    Claiming the whole batch atomically is what makes parallel dispatch safe:
    either this worker owns all of them or it owns none and re-reads. A
    per-node write would leave a window where two workers each claim a
    different half of the same ready set from the same stale snapshot.

    Returns True when the claim landed, False on a lost race (caller re-reads).
    """
    from orchestrator_next import state_store

    handle = state_store.parse_handle(state_yaml_path)
    store, h = state_store.open_store(handle)
    try:
        _fresh, token = store.load(h)
    except (state_store.StateNotFoundError, OSError):
        return False
    for step_id in step_ids:
        readiness.mark_node_status(state_raw, phase, step_id, "in_progress")
    try:
        store.save(h, state_raw, token)
    except state_store.StateConflictError:
        return False
    except yaml.YAMLError:
        return False  # file backend already restored the prior bytes
    return True


def _persist_blocked_status(state_yaml_path: str, state_raw: dict) -> None:
    """Best-effort: mark state.yaml status=blocked when the spawn-failure cap
    fires, mirroring the retry-cap path in record.py (~:624) which persists
    blocked so a consumer reading state.yaml alone (not just the CLI exit
    code) can see the run is stuck. Uses the same load/save-with-token path
    as _claim_nodes; a lost race or missing file is tolerated — the CLI exit
    code 2 remains the authoritative signal either way.
    """
    from orchestrator_next import state_store

    handle = state_store.parse_handle(state_yaml_path)
    try:
        store, h = state_store.open_store(handle)
        fresh, token = store.load(h)
    except (state_store.StateNotFoundError, OSError):
        return
    fresh["status"] = "blocked"
    try:
        store.save(h, fresh, token)
    except (state_store.StateConflictError, yaml.YAMLError):
        pass


def _build_action_base(
    contract: StepContract,
    step_id: str,
    phase: str,
    attempt: int,
    state: State,
    state_yaml_path: str,
) -> dict[str, Any]:
    """Build the base keys shared by both resume and fresh-dispatch action dicts.

    Resume path adds: is_resume, started_at, model.
    Fresh path adds: model (agent step) or run (script step).
    """
    env = _build_dispatch_env(state, step_id, attempt, state_yaml_path)
    # The map goes to script steps too: persist-learnings resolves its append
    # targets from it. Only ORCHESTRATOR_PROMPT_DIR (this step's own dir) is
    # agent-only — script steps have no prompt of their own.
    prompt_dirs = _prompt_dir_map(state)
    if prompt_dirs:
        env["ORCHESTRATOR_PROMPT_DIRS"] = json.dumps(prompt_dirs, sort_keys=True)
    if isinstance(contract, AgentStepContract) and contract.prompt_dir:
        env["ORCHESTRATOR_PROMPT_DIR"] = contract.prompt_dir
    return {
        "step_id": step_id,
        "phase": phase,
        "attempt": attempt,
        "instruction": contract.instruction if isinstance(contract, AgentStepContract) else "",
        "env": env,
        "step_context": _node_step_context(state, step_id),
        "prompt_dir": (
            contract.prompt_dir if isinstance(contract, AgentStepContract) else None
        ),
    }


def _handle_resume(
    state: State, state_yaml_path: str, last: StepHistoryEntry
) -> tuple[dict[str, Any], int]:
    """Resume an in-progress step.

    Keeps the ORIGINAL attempt number — do not recompute it via compute_attempt
    (that returns max+1, which is retry semantics, not resume semantics).
    """
    step_id = last.step_id
    attempt = last.attempt if last.attempt is not None else 1
    try:
        contract = load_contract_for_step(step_id)
    except FileNotFoundError:
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
        action["model"] = _resolved_model(step_id, contract)
    return action, 0


def _dispatch_fresh(
    state: State, state_yaml_path: str, next_step_id: str, *, claim: bool = True
) -> tuple[dict[str, Any], int]:
    """Dispatch a fresh (non-resume) step node.

    `claim=False` builds the action without writing the in_progress claim —
    used by `dispatch_batch`, which claims the whole batch in one write.
    """
    contract = load_contract_for_step(next_step_id)

    spawn_failures = _consecutive_spawn_failures(
        state.step_history, state.phase, next_step_id
    )
    if spawn_failures >= _DEFAULT_MAX_SPAWN_FAILURES:
        print(
            f"BLOCKED: spawn_failure_cap — {spawn_failures} consecutive zero-token "
            f"failures for {state.phase}/{next_step_id}",
            file=sys.stderr,
        )
        _persist_blocked_status(state_yaml_path, state.raw)
        return {"reason": "spawn_failure_cap"}, 2

    attempt = compute_attempt(state.step_history, state.phase, next_step_id, include_in_progress=True)

    action = _build_action_base(
        contract,
        next_step_id,
        state.phase,
        attempt,
        state,
        state_yaml_path,
    )
    if isinstance(contract, AgentStepContract):
        action["model"] = _resolved_model(next_step_id, contract)
    else:
        action["run"] = contract.run

    if claim:
        _persist_node_status(state_yaml_path, state.phase, next_step_id, state_raw=state.raw)
    return action, 0


def dispatch(state: State, state_yaml_path: str) -> tuple[dict[str, Any], int]:
    """DAG-walk dispatcher: State → (action_dict, exit_code).

    exit 0 + JSON with agent key → driver spawns Agent tool
    exit 0 + no JSON → inline script ran and recorded; driver loops
    exit 1 → workflow complete
    exit 2 → step blocked
    exit 3 → ContractDispatchError
    """
    last = state.step_history[-1] if state.step_history else None

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
        return {}, 1

    return _dispatch_fresh(state, state_yaml_path, next_step_id)


MAX_CLAIM_RETRIES = 8


def dispatch_batch(
    state_yaml_path: str, *, max_parallel: int = 1
) -> tuple[list[dict[str, Any]], int]:
    """Claim up to `max_parallel` ready steps in one atomic write.

    Serial equivalence: with `max_parallel == 1` this returns exactly what
    `dispatch()` returns, wrapped in a list — same blocking checks, same resume
    path, same exit codes. Parallelism is opt-in and the serial path is
    unchanged, which is why the existing suite still passes untouched.

    Exit codes match `dispatch()`: 0 actions, 1 complete, 2 blocked, 3 contract.

    A step that is `in_progress` is a claim held by someone else, so the batch
    ready-set excludes it (`ready_nodes(exclude_claimed=True)`). The single
    exception is the resume path, which deliberately re-dispatches an
    `in_progress` step whose process died — that is handled before we get here,
    by `dispatch()`.
    """
    from orchestrator_next.parser import load_state

    for _attempt in range(MAX_CLAIM_RETRIES):
        state = load_state(state_yaml_path)

        # Blocking status and crash-resume are inherently serial decisions —
        # defer to dispatch() so there is exactly one implementation of each.
        last = state.step_history[-1] if state.step_history else None
        if last is not None and last.phase == state.phase and (
            last.status in _BLOCKING_STATUSES
            # await_input keeps its node in_progress on purpose (the same step
            # resumes with the user's answer) — a claim-excluding batch would
            # skip it forever, so defer to the serial resume path.
            or last.status == "await_input"
            or (last.status == "in_progress" and last.ended_at is None)
        ):
            action, code = dispatch(state, state_yaml_path)
            return ([action] if code == 0 else []), code

        if max_parallel <= 1:
            action, code = dispatch(state, state_yaml_path)
            return ([action] if code == 0 else []), code

        ready = readiness.ready_nodes(state, exclude_claimed=True)
        if not ready:
            # Nothing claimable. Either the phase is done, or every remaining
            # node is claimed by a worker still running — the caller decides
            # which by checking whether it has work in flight.
            return [], 1 if not readiness.ready_nodes(state) else 0

        chosen = ready[:max_parallel]
        actions: list[dict[str, Any]] = []
        for step_id in chosen:
            action, code = _dispatch_fresh(state, state_yaml_path, step_id,
                                           claim=False)
            if code != 0:
                # A spawn-failure cap (or contract problem) on any node is a
                # whole-run condition; surface it rather than silently running
                # the rest of the batch.
                return ([], code) if code != 0 else ([action], code)
            actions.append(action)

        if _claim_nodes(state_yaml_path, state.phase, chosen, state_raw=state.raw):
            return actions, 0
        # Lost the race — someone claimed part of our set. Re-read and retry.

    return [], 2


def emit_json(obj: dict[str, Any]) -> str:
    """
    Emit the action dict as deterministic, sorted-keys, indented JSON.

    Always uses sort_keys=True and indent=2 for byte-identical output
    regardless of TTY — deterministic, byte-identical for test comparison.
    """
    return json.dumps(obj, sort_keys=True, indent=2) + "\n"
