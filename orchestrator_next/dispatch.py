"""
Pure dispatcher: State → (action_dict, exit_code).

Two-path dispatch protocol:
  exit 0 + JSON action          → the caller hands it to the driver
  exit 0 + no JSON              → inline script ran and recorded; driver loops
  exit 1                        → workflow complete; driver reads state.yaml
  exit 2                        → step blocked; driver reads state.yaml
  exit 3                        → ContractDispatchError (no prompt: or run:)
  exit 4                        → next step `requires:` an unapproved gate token

No action field. No signal field. No verify_phase.
"""
from __future__ import annotations

import json
import sys
from typing import Any

import yaml

from orchestrator_next import readiness
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


def _step_in_plan(state, phase: str, step_id: str) -> bool:
    """True when step_id is a node in workflow_plan for this phase."""
    return any(str(n.get("id", "")) == step_id for n in phase_nodes(state, phase))


# A ready step whose `requires:` gate token has not been approved. Not a
# failure and not a block: the run resumes the moment someone approves, so it
# gets its own code rather than being folded into exit 2 (halt).
EXIT_GATE_REQUIRED = 4

# The phase has no ready node, but it did not finish either: a node ended
# `abandoned` and its dependents can never become ready. Exit 1 here would
# tell the harness the run completed successfully, so a dead end gets its own
# code and the human gets the abandoning step's reason.
EXIT_NEEDS_YOU = 5


class ContractDispatchError(RuntimeError):
    """Missing step contract or agent file on disk; run /doctor to diagnose."""


# Blocking statuses: caller cannot proceed
_BLOCKING_STATUSES = frozenset({"escalate_to_architect", "blocked"})
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



# Distinguishes "caller supplied no token" from a legitimately falsy token.
_UNSET: Any = object()


def _skip_unchanged(
    state: State, ready: list[str], state_yaml_path: str, token: Any = _UNSET
) -> tuple[list[str], bool]:
    """Drop ready nodes whose recorded artifacts still hash the same.

    A re-queued node that already has `artifacts:` recorded, whose declared
    in/out files are byte-identical to what the step last produced, has nothing
    left to do. Marking it completed here is what makes a resume cheap instead
    of a full re-run.

    The skip mutates `state.raw`, so it is written back through `_claim_nodes` —
    the same compare-and-swap save a claim uses. A lost race is harmless: the
    nodes stay pending and the next pass re-evaluates them.

    Returns `(ids still needing dispatch, whether a skip was written)`. The
    second element matters to a caller holding a CAS token: the write spends
    that token, so the caller must re-read before claiming anything.
    """
    from orchestrator_next.artifacts import node_is_unchanged
    from orchestrator_next.protocol import _artifact_base

    nodes = phase_nodes(state, state.phase)
    skipped: list[str] = []
    remaining: list[str] = []
    base: Any = None

    for step_id in ready:
        node = readiness.find_node(nodes, step_id)
        if not node or not node.get("artifacts"):
            remaining.append(step_id)
            continue
        if node.get("status") == "reset":
            # `reset` is the router saying "run this again" after a failure or
            # a rejected verdict. The files are byte-identical to what the step
            # last produced — that is precisely why it is being re-run — so the
            # idempotency check would call it unchanged, mark it completed, and
            # skip the re-review the rework loop exists to perform.
            remaining.append(step_id)
            continue
        try:
            contract = load_contract_for_step(step_id)
        except Exception:
            # No readable contract means nothing to compare against; let the
            # normal dispatch path raise the real error.
            remaining.append(step_id)
            continue
        if base is None:
            base = _artifact_base(state.raw)
        try:
            unchanged = node_is_unchanged(node, contract, base)
        except OSError:
            unchanged = False
        if not unchanged:
            remaining.append(step_id)
            continue
        readiness.mark_node_status(state.raw, state.phase, step_id, "completed")
        # find_node returns the live dict inside state.raw, so this lands in
        # the document the CAS save writes.
        node["skipped_unchanged"] = True
        skipped.append(step_id)

    if skipped:
        # Attempt the write either way; a conflict just means the next pass
        # re-evaluates. The token is spent regardless, hence the flag.
        _claim_nodes(state_yaml_path, state.phase, [], state_raw=state.raw,
                     token=token)
    return remaining, bool(skipped)


def _persist_node_status(
    state_yaml_path: str,
    phase: str,
    step_id: str,
    state_raw: dict,
) -> None:
    """Claim a node by marking it in_progress in the state store."""
    _claim_nodes(state_yaml_path, phase, [step_id], state_raw=state_raw)


def read_claim_token(state_yaml_path: str):
    """Return the store token for the snapshot a claim decision will be made on.

    The caller must take this token BEFORE choosing which nodes to claim, and
    hand it back to `_claim_nodes`. Taking it inside the claim instead would
    compare against a row that may already have moved since the decision, which
    validates nothing — see `_claim_nodes`.
    """
    from orchestrator_next import state_store

    handle = state_store.parse_handle(state_yaml_path)
    store, h = state_store.open_store(handle)
    try:
        _doc, token = store.load(h)
    except (state_store.StateNotFoundError, OSError):
        return None
    return token


def _claim_nodes(
    state_yaml_path: str,
    phase: str,
    step_ids: list[str],
    state_raw: dict,
    token: Any = _UNSET,
) -> bool:
    """Mark every id in `step_ids` in_progress in ONE compare-and-swap write.

    Claiming the whole batch atomically is what makes parallel dispatch safe:
    either this worker owns all of them or it owns none and re-reads. A
    per-node write would leave a window where two workers each claim a
    different half of the same ready set from the same stale snapshot.

    `token` MUST be the token read at the same time as the snapshot the claim
    decision was made from (see `read_claim_token`). Re-reading the token here
    would defeat the compare-and-swap: a worker that decided on version N, then
    entered the claim after a rival committed N+1, would read N+1, save cleanly,
    and both workers would believe they owned the batch.

    Returns True when the claim landed, False on a lost race (caller re-reads).
    """
    from orchestrator_next import state_store

    handle = state_store.parse_handle(state_yaml_path)
    store, h = state_store.open_store(handle)
    if token is _UNSET:
        # Serial callers that hold no snapshot token: read one now. Safe only
        # because nothing else is dispatching concurrently on that path.
        try:
            _doc, token = store.load(h)
        except (state_store.StateNotFoundError, OSError):
            return False
    if token is None:
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

    Resume path adds: is_resume, started_at.
    Fresh path adds: run (script step).
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
        "prompt_path": (
            contract.prompt_path if isinstance(contract, AgentStepContract) else ""
        ),
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
        contract = AgentStepContract(id=step_id)
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
    return action, 0


def _dispatch_fresh(
    state: State, state_yaml_path: str, next_step_id: str, *, claim: bool = True
) -> tuple[dict[str, Any], int]:
    """Dispatch a fresh (non-resume) step node.

    `claim=False` builds the action without writing the in_progress claim —
    used by `dispatch_batch`, which claims the whole batch in one write.
    """
    from orchestrator_next import gates

    node = readiness.find_node(phase_nodes(state, state.phase), next_step_id) or {}

    # A `requires:` token that is not approved yet means the human has not
    # signed off on the gate that guards this step. That is a decision the
    # engine cannot make, hence needs_you rather than a failure.
    required = str(node.get("requires") or "")
    if required and not gates.token_is_approved(state.raw, required):
        return {
            "step_id": next_step_id,
            "reason": "gate_token_required",
            "requires": required,
            "detail": (
                f"step {next_step_id!r} requires the {required!r} gate token; "
                f"approve the gate that declares approve_as: {required}"
            ),
        }, 4

    # A gate is pure recipe metadata: no contract file to load, nothing to run.
    if gates.node_is_gate(node):
        return {
            "step_id": next_step_id,
            "phase": state.phase,
            "kind": gates.GATE_KIND,
            "show": [str(s) for s in (node.get("show") or [])],
            "approve_as": str(node.get("approve_as") or ""),
        }, 0

    contract = load_contract_for_step(next_step_id)

    attempt = compute_attempt(state.step_history, state.phase, next_step_id, include_in_progress=True)

    action = _build_action_base(
        contract,
        next_step_id,
        state.phase,
        attempt,
        state,
        state_yaml_path,
    )
    if not isinstance(contract, AgentStepContract):
        action["run"] = contract.run

    if claim:
        _persist_node_status(state_yaml_path, state.phase, next_step_id, state_raw=state.raw)
    return action, 0


def dispatch(state: State, state_yaml_path: str) -> tuple[dict[str, Any], int]:
    """DAG-walk dispatcher: State → (action_dict, exit_code).

    exit 0 + JSON action → the caller hands it to the driver
    exit 0 + no JSON → inline script ran and recorded; driver loops
    exit 1 → workflow complete
    exit 2 → step blocked
    exit 3 → ContractDispatchError
    exit 4 → next step requires an unapproved gate token
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

    # Completing a node unblocks its dependents, so a skip makes the ready set
    # stale. Drain until a pass skips nothing, otherwise a chain of unchanged
    # nodes would report the phase complete with successors still pending. Each
    # pass completes at least one node, so this terminates.
    for _ in range(len(phase_nodes(state, state.phase)) + 1):
        ready, skipped = _skip_unchanged(
            state, readiness.ready_nodes(state), state_yaml_path
        )
        if not skipped:
            break

    if not ready:
        abandoned = readiness.abandoned_nodes(state)
        if abandoned:
            # Not a finished run: some node gave up and everything downstream
            # of it is permanently unreachable. Hand the human the reason it
            # recorded rather than reporting success.
            reason = str(state.raw.get("needs_you_reason") or "").strip()
            return {
                "step_id": abandoned[0],
                "reason": "abandoned_dead_end",
                "detail": reason or f"{abandoned[0]} abandoned",
            }, EXIT_NEEDS_YOU
        return {}, 1

    return _dispatch_fresh(state, state_yaml_path, ready[0])


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
        # Take the CAS token FIRST, then read the snapshot we decide on. Any
        # rival write that lands between here and our claim moves the row past
        # this token, so the claim's save is rejected and we re-read. Reading
        # the token later (inside the claim) would silently validate nothing.
        claim_token = read_claim_token(state_yaml_path)
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
            return ([action] if code in (0, EXIT_GATE_REQUIRED) else []), code

        if max_parallel <= 1:
            action, code = dispatch(state, state_yaml_path)
            return ([action] if code in (0, EXIT_GATE_REQUIRED) else []), code

        ready, skipped = _skip_unchanged(
            state,
            readiness.ready_nodes(state, exclude_claimed=True),
            state_yaml_path,
            token=claim_token,
        )
        if skipped:
            # The skip spent our CAS token. Re-read before claiming, so the
            # claim compares against the version the skip just produced.
            continue
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

        if _claim_nodes(state_yaml_path, state.phase, chosen, state_raw=state.raw,
                        token=claim_token):
            return actions, 0
        # Lost the race — someone claimed part of our set. Re-read and retry.

    return [], 2
