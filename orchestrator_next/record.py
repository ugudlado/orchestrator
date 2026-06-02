"""HL-287 M5: orchestrator record subcommand.

Accepts a completed step's outputs + usage + evidence via stdin JSON,
validates against the contract's `expected_outputs`, writes a terminal
`step_history` entry with uniform `started_at` / `completed_at` / `usage`,
and advances `next_step` per `workflow_plan`.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from orchestrator_next.parser import ContractError, State, load_contract_for_step
from orchestrator_next import readiness


# Optional fields copied from done payload into step_history[-1].
_OPTIONAL_STEP_HISTORY_KEYS = (
    "artifacts",
    "review_score",
    "approach",
    "regression",
    "rollback",
    "retry_context",
    "regression_check",
    "blocker",
    "escalation",
)


_STATE_PATCH_KEYS = frozenset({
    "retries",
    "quarantine_events",
    "baseline",
    "refresh_artifacts",
    "change_type",
    "flag_adaptations",
    "worktree_path",
    "branch",
})

def _resolve_append_retro_script(repo_root: str) -> str:
    """Path to append-retro.sh for workflow_issues handling."""
    candidates: list[str] = []
    home = os.environ.get("ORCHESTRATOR_HOME", "")
    if home:
        candidates.append(
            os.path.join(home, "orchestrator_next", "scripts", "complete", "append-retro.sh")
        )
    if repo_root:
        candidates.append(
            os.path.join(repo_root, "orchestrator_next", "scripts", "complete", "append-retro.sh")
        )
    for path in candidates:
        if os.path.isfile(path):
            return path
    return ""

def _resolve_agent_id(payload: dict[str, Any]) -> str | None:
    """agent_id from explicit payload fields (shell-driver done payloads)."""
    usage = payload.get("usage") or {}
    return payload.get("agent_id") or usage.get("agent_id")


def _usage_has_tokens(usage: dict[str, Any]) -> bool:
    return (
        (isinstance(usage.get("input_tokens"), (int, float)) and usage["input_tokens"] > 0)
        or (isinstance(usage.get("output_tokens"), (int, float)) and usage["output_tokens"] > 0)
    )


def _validate_phase_review_output(
    step_id: str, outputs: dict[str, Any]
) -> tuple[dict[str, Any], int] | None:
    """Reject invalid phase_review_report.verdict at the record boundary."""
    if step_id != "run-phase-review":
        return None
    report = outputs.get("phase_review_report")
    if not isinstance(report, dict):
        return (
            {
                "reason": "invalid_phase_review_report",
                "step_id": step_id,
                "hint": "outputs.phase_review_report must be an object with verdict",
            },
            3,
        )
    verdict = report.get("verdict")
    if not isinstance(verdict, str) or verdict not in _PHASE_REVIEW_VERDICTS:
        return (
            {
                "reason": "invalid_phase_review_verdict",
                "step_id": step_id,
                "verdict": verdict,
                "valid_verdicts": sorted(_PHASE_REVIEW_VERDICTS),
            },
            3,
        )
    return None


# ---------------------------------------------------------------------------
# orc-67: run-phase-review needs_work rework loop
# ---------------------------------------------------------------------------

# Fallback when project.yaml omits quality_bar.max_retry_rounds. Matches the
# historical verify_block.max_retries default; the repo's own project.yaml
# sets 8, so this only fires on a misconfigured repo / test fixture.
_DEFAULT_MAX_RETRY_ROUNDS = 3

# Map every payload `status` the driver may send to the state.yaml.status it
# implies (None = no state-level change). Single source of truth for both
# validation and FR-2 halt semantics — keeping them in one dict prevents the
# two from drifting as new terminal statuses are added.
_STATUS_TO_STATE_STATUS: dict[str, str | None] = {
    "completed": None,
    "recovered": None,
    # abandoned is routed via on_failure; block only on explicit halt paths.
    "abandoned": None,
    "failed": None,           # routing handles failure; _resolve_routing sets blocked when halting
    "blocked": "blocked",
    "escalate_to_architect": "blocked",
}


def _coerce_payload_outputs(raw: Any) -> dict[str, Any]:
    """Normalize payload.outputs to a mapping for step_history evidence."""
    if isinstance(raw, dict):
        return raw
    if raw is not None:
        sys.stderr.write(
            f"[record] warning: outputs must be a mapping, got {type(raw).__name__}; "
            "treating as empty\n"
        )
    return {}


def _artifact_basenames_from_outputs(outputs: dict[str, Any]) -> list[str]:
    """Infer artifact filenames from COMPLETION output keys/values."""
    skip_keys = frozenset({
        "updated_artifact_set",
        "design_direction",
        "complexity",
        "discovery_result",
    })
    names: list[str] = []
    for key, val in outputs.items():
        if key in skip_keys:
            continue
        if isinstance(key, str) and "." in key and not key.startswith("{"):
            names.append(Path(key).name)
            continue
        if isinstance(val, str) and val.strip():
            candidate = Path(val.strip()).name
            if "." in candidate:
                names.append(candidate)
    seen: set[str] = set()
    ordered: list[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def _supplement_legacy_outputs(
    outputs: dict[str, Any],
    payload: dict[str, Any],
    contract: StepContract | None,
) -> dict[str, Any]:
    """Fill missing legacy output keys from artifacts list or output paths.

    design-and-draft-artifacts agents often list paths under outputs but omit
    ``updated_artifact_set`` (and sometimes ``artifacts``); record would reject
    with missing_outputs even when design.md and tasks.yaml exist on disk.
    """
    if contract is None:
        return outputs
    out = dict(outputs)
    if "updated_artifact_set" not in contract.legacy_output_names:
        return out
    cur = out.get("updated_artifact_set")
    empty = cur is None or (hasattr(cur, "__len__") and len(cur) == 0)
    if not empty:
        return out

    candidates: list[str] = []
    artifacts = payload.get("artifacts")
    if isinstance(artifacts, list):
        candidates.extend(str(a) for a in artifacts if a is not None)
    candidates.extend(_artifact_basenames_from_outputs(out))

    if not candidates:
        return out

    out["updated_artifact_set"] = list(dict.fromkeys(candidates))
    if not payload.get("artifacts"):
        payload["artifacts"] = list(out["updated_artifact_set"])
    sys.stderr.write(
        "[record] supplemented outputs.updated_artifact_set "
        "from payload outputs/artifacts\n"
    )
    return out


# Declared outputs that may be an empty list (contract allows "no tickets synced").
_OUTPUTS_ALLOW_EMPTY_LIST: frozenset[str] = frozenset({"backlog_tickets_synced"})


def _supplement_learn_result(
    outputs: dict[str, Any],
    payload: dict[str, Any],
    step_id: str,
    status: str,
) -> dict[str, Any]:
    """run-learn-cycle: ensure learn_result when the agent omitted outputs:."""
    if step_id != "run-learn-cycle":
        return outputs
    out = dict(outputs)
    cur = out.get("learn_result")
    if cur is not None and cur != "" and not (hasattr(cur, "__len__") and len(cur) == 0):
        return out
    if status != "completed":
        return out
    out["learn_result"] = {"completed": True}
    sys.stderr.write(
        "[record] supplemented outputs.learn_result for run-learn-cycle "
        "(COMPLETION missing outputs:; treated as learn completed)\n"
    )
    return out


def _supplement_backlog_tickets_synced(
    outputs: dict[str, Any],
    step_id: str,
    status: str,
) -> dict[str, Any]:
    """run-learn-cycle: ensure backlog_tickets_synced when the agent omitted it."""
    if step_id != "run-learn-cycle" or status != "completed":
        return outputs
    out = dict(outputs)
    if "backlog_tickets_synced" in out:
        return out
    out["backlog_tickets_synced"] = []
    sys.stderr.write(
        "[record] supplemented outputs.backlog_tickets_synced=[] for "
        "run-learn-cycle (COMPLETION missing backlog sync list)\n"
    )
    return out


def _merge_evidence_block(
    outputs: dict[str, Any],
    raw_evidence: Any,
) -> dict[str, Any]:
    """Build step_history evidence from payload outputs + optional evidence block.

    Drivers sometimes emit ``evidence`` as a YAML list of command records;
    spreading that dict would raise TypeError. Lists are stored under
    ``evidence.commands``; mappings are merged with payload outputs winning on
    key overlap.
    """
    if raw_evidence is None:
        return {"outputs": outputs}
    if isinstance(raw_evidence, dict):
        merged = dict(raw_evidence)
        prior = merged.get("outputs")
        if isinstance(prior, dict):
            merged["outputs"] = {**prior, **outputs}
        else:
            merged["outputs"] = outputs
        return merged
    if isinstance(raw_evidence, list):
        sys.stderr.write(
            "[record] warning: evidence must be a mapping; "
            "storing list under evidence.commands\n"
        )
        return {"outputs": outputs, "commands": raw_evidence}
    return {"outputs": outputs, "detail": raw_evidence}



def _max_retry_rounds(state_raw: dict[str, Any]) -> int:
    """Read `quality_bar.max_retry_rounds` from the repo's project.yaml (orc-67)."""
    from orchestrator_next.dispatch import _project_yaml_path

    candidate = _project_yaml_path(state_raw)
    if candidate is not None:
        try:
            data = yaml.safe_load(candidate.read_text()) or {}
            quality_bar = data.get("quality_bar") if isinstance(data, dict) else None
            value = quality_bar.get("max_retry_rounds") if isinstance(quality_bar, dict) else None
            if isinstance(value, int) and not isinstance(value, bool):
                return value
        except (yaml.YAMLError, OSError) as exc:
            sys.stderr.write(f"[record] warning: could not read {candidate}: {exc}\n")

    sys.stderr.write(
        f"[record] warning: quality_bar.max_retry_rounds not found in project.yaml; "
        f"defaulting to {_DEFAULT_MAX_RETRY_ROUNDS}\n"
    )
    return _DEFAULT_MAX_RETRY_ROUNDS


def _apply_state_patch(state_raw: dict[str, Any], patch: dict[str, Any]) -> None:
    """Apply state_patch from orchestrator done payload into top-level state."""
    if not isinstance(patch, dict):
        return
    for key in patch:
        if key not in _STATE_PATCH_KEYS:
            sys.stderr.write(
                f"[record] warning: state_patch key {key!r} ignored "
                f"(allowed: {sorted(_STATE_PATCH_KEYS)})\n"
            )
    retries = patch.get("retries")
    if isinstance(retries, dict):
        existing = state_raw.get("retries") or {}
        if not isinstance(existing, dict):
            existing = {}
        # Per-key replace: payload sends absolute retry counts, not deltas.
        existing.update(retries)
        state_raw["retries"] = existing
    qe = patch.get("quarantine_events")
    if qe is not None:
        events = state_raw.get("quarantine_events") or []
        if not isinstance(events, list):
            events = []
        if isinstance(qe, list):
            events.extend(qe)
        else:
            events.append(qe)
        state_raw["quarantine_events"] = events
    for key in ("baseline", "refresh_artifacts", "change_type", "flag_adaptations", "worktree_path", "branch"):
        if key in patch:
            state_raw[key] = patch[key]


# ---------------------------------------------------------------------------
# Statechart routing (on_success / on_failure edges)
# ---------------------------------------------------------------------------

_SUCCESS_STATUSES = frozenset({"completed", "recovered"})
_HALT_KEYWORD = "halt"


def _find_workflow_node(state_raw: dict[str, Any], phase: str, step_id: str) -> dict[str, Any] | None:
    """Return the node dict for step_id in workflow_plan[phase].nodes, or None."""
    phase_block = (state_raw.get("workflow_plan") or {}).get(phase)
    if not isinstance(phase_block, dict):
        return None
    for node in phase_block.get("nodes") or []:
        if isinstance(node, dict) and str(node.get("id", "")) == step_id:
            return node
    return None


_HALT_CAP_EXCEEDED = "halt_cap_exceeded"  # sentinel: cap exhaustion (entry status → blocked)


def _resolve_routing(
    step_id: str,
    status: str,
    state_raw: dict[str, Any],
    phase: str,
) -> str:
    """Determine where the workflow goes after a step completes.

    Returns one of:
      - "advance"          — mark node completed, let next_ready_node() pick the next
      - "halt"             — mark node completed, set state.status = blocked (keep entry status)
      - "halt_cap_exceeded"— cap exhaustion: halt AND rewrite entry status to blocked
      - "<step_id>"        — activate that node (mark it pending), loop back

    Decision order:
      1. on_success / on_failure edge declared in the workflow node.
      2. Retry cap: if on_failure points to an earlier step and retries are
         exhausted, escalate to halt_cap_exceeded.
      3. Default: "advance" on success, "halt" on failure.
    """
    success = status in _SUCCESS_STATUSES
    node = _find_workflow_node(state_raw, phase, step_id)
    edge_key = "on_success" if success else "on_failure"
    target = (node or {}).get(edge_key)  # None = no explicit edge

    if target is None:
        return "advance" if success else _HALT_KEYWORD

    if str(target) == _HALT_KEYWORD:
        return _HALT_KEYWORD

    target = str(target)

    # Loop target (on_failure pointing back) — enforce retry cap.
    if not success:
        max_r = int((node or {}).get("max_retries") or _max_retry_rounds(state_raw))
        retries_map = state_raw.setdefault("retries", {})
        if not isinstance(retries_map, dict):
            retries_map = {}
            state_raw["retries"] = retries_map
        count = retries_map.get(step_id, 0)
        if not isinstance(count, int):
            count = 0
        if count >= max_r:
            sys.stderr.write(
                f"[record] {step_id}: on_failure retry cap reached "
                f"({count}/{max_r}), escalating to halt\n"
            )
            return _HALT_CAP_EXCEEDED
        retries_map[step_id] = count + 1

    return target


# ---------------------------------------------------------------------------
# Boundary detection (FR-4)
# ---------------------------------------------------------------------------

class BoundaryKind(str, Enum):
    NONE = "none"
    PHASE = "phase"
    FEATURE = "feature"


def _phase_node_ids(workflow_plan: dict, phase: str) -> list[str]:
    """Return the ordered step ids for a phase from `workflow_plan`.

    ORC-63: reads the `nodes` list (post-promotion shape). Accepts a legacy
    `active:[ids]` block as a back-compat read path. Returns [] when neither.
    """
    phase_block = workflow_plan.get(phase) or {}
    if not isinstance(phase_block, dict):
        return []
    nodes = phase_block.get("nodes")
    if nodes is not None:
        return [str(n.get("id", "")) for n in nodes if isinstance(n, dict)]
    active = phase_block.get("active") or []
    return [str(s) for s in active]


def _detect_boundary(
    workflow_plan: dict,
    phase: str,
    step_id: str,
    status: str,
) -> BoundaryKind:
    """Return BoundaryKind based on workflow_plan and current step (ORC-63).

    Returns NONE for any status != 'completed'.
    Returns NONE if step_id is not the last declaration-order node in the phase.
    Returns PHASE if step_id is the last node AND phase is not the last key.
    Returns FEATURE if step_id is the last node AND phase IS the last key.

    "Last node" = the last entry of `workflow_plan[phase].nodes` (a legacy
    `active:[ids]` block is read via the back-compat path).
    """
    if status != "completed":
        return BoundaryKind.NONE

    node_ids = _phase_node_ids(workflow_plan, phase)
    if not node_ids or step_id != node_ids[-1]:
        return BoundaryKind.NONE

    # step_id is the last node — at minimum a phase boundary
    phase_keys = list(workflow_plan.keys())
    if phase_keys and phase == phase_keys[-1]:
        return BoundaryKind.FEATURE
    return BoundaryKind.PHASE


# ---------------------------------------------------------------------------
# Cost computation helpers (ISSUE-17)
# ---------------------------------------------------------------------------
# Extracted to orchestrator_next.pricing (ORC-71). Re-exported here by reference
# so existing call sites and test fixtures (`_record_mod._pricing_cache.clear()`,
# `_load_routes.cache_clear()`) keep acting on the live objects the production
# path uses — Python imports bind the same object.
from orchestrator_next.pricing import (  # noqa: E402,F401
    _orchestrator_home,
    _load_routes,
    _lookup_price,
    _billable_token_units,
    _compute_cost_usd,
)

# ---------------------------------------------------------------------------
# Feature metrics (ORC-74). Re-exported by reference — same object identity.
# ---------------------------------------------------------------------------
from orchestrator_next.metrics import (  # noqa: E402,F401
    _PHASE_REVIEW_VERDICTS,
    _phase_review_verdict,
    compute_task_counts,
    compute_retries,
    compute_resolution,
    run_git_churn,
    extract_review_scores,
    wall_clock_minutes,
    _resolve_workflow_artifact_path,
    _resolve_feature_metrics_tasks_path,
    _resolve_feature_metrics,
)


def _utcnow_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_tasks_md(state_raw: dict[str, Any]) -> Path | None:
    """Thin wrapper: resolve tasks.md path from state.yaml fields."""
    return _resolve_workflow_artifact_path(state_raw, "tasks.md")


REPEAT_PREDICATES: dict[str, Any] = {
    # ORC-65: checkbox-counting predicate removed; task completion is now tracked
    # via per-task step_history entries (task-T-N nodes in workflow_plan).
    # Other steps may still declare repeat_until predicates — register them here.
}


def _check_declared_outputs(
    declared: list[Any], outputs: dict[str, Any], state_raw: dict[str, Any]
) -> list[str]:
    """Return the list of declared outputs that are not verifiably satisfied (AC-10).

    `declared` may be either:
      - list[dict[str, Any]] (Stage B typed spec: each item has 'name' and
        optionally 'path'), or
      - list[str] (legacy bare names, preserved for back-compat).

    For typed entries with a non-None `path`:
      - Substitute '<slug>' → state_raw['change_id'] in the path.
      - Resolve relative paths against worktree_path or repo_root.
      - Require os.path.isfile(resolved_path); key presence in outputs is NOT
        required (the file on disk is the truth signal).

    For legacy entries (bare strings or dicts without path):
      - Key must be present in outputs, AND
      - Value must be non-null and non-empty, AND
      - If the name itself contains '/' (old path-named heuristic), the file
        must exist on disk.
    """
    unsatisfied: list[str] = []
    base = state_raw.get("worktree_path") or state_raw.get("repo_root") or ""
    if isinstance(base, str) and base.startswith("~"):
        base = os.path.expanduser(base)
    change_id = state_raw.get("change_id") or ""

    for item in declared:
        if isinstance(item, dict):
            name = str(item.get("name", ""))
            path = item.get("path")
            if path is not None:
                # Typed spec: check file existence, ignore evidence.outputs key.
                resolved = str(path).replace("<slug>", change_id)
                candidate = Path(resolved)
                if not candidate.is_absolute() and base:
                    candidate = Path(base) / resolved
                if not candidate.is_file():
                    unsatisfied.append(name)
                continue
            # Dict without path: fall through to legacy value-presence check.
        else:
            name = str(item)

        # Legacy check: key must be present, value non-null/non-empty.
        if name not in outputs:
            unsatisfied.append(name)
            continue
        value = outputs[name]
        # Reject null / empty (empty str, empty list/dict). Zero/False are real.
        if value is None or (
            hasattr(value, "__len__")
            and len(value) == 0
            and name not in _OUTPUTS_ALLOW_EMPTY_LIST
        ):
            unsatisfied.append(name)
            continue
        # Path-named output (legacy heuristic): the file must exist on disk.
        if "/" in name:
            candidate = Path(name)
            if not candidate.is_absolute() and base:
                candidate = Path(base) / name
            if not candidate.is_file():
                unsatisfied.append(name)
    return unsatisfied


def _state_from_raw(state_raw: dict[str, Any]) -> State:
    """Build an in-memory State view over a mutated `state_raw` dict (ORC-63).

    `readiness` functions operate on a `State`; this avoids re-reading the
    on-disk state.yaml (which would be stale relative to in-flight mutations).
    Only the fields readiness needs are populated accurately.
    """
    return State(
        change_id=state_raw.get("change_id", ""),
        phase=state_raw.get("phase", ""),
        repo_root=str(state_raw.get("repo_root") or ""),
        workflow_dir=str(state_raw.get("worktree_path") or ""),
        workflow_plan=state_raw.get("workflow_plan", {}) or {},
        step_history=[],
        raw=state_raw,
    )


def _repeat_until_pending(
    step_id: str, state_yaml_path: str, state_raw: dict[str, Any]
) -> bool:
    """Return True iff `step_id` declares a repeat_until predicate that is
    currently False — i.e. the step must be re-run, not advanced past."""
    try:
        contract = load_contract_for_step(step_id, state_yaml_path)
    except (FileNotFoundError, ContractError):
        return False
    if not (contract and contract.repeat_until):
        return False
    predicate = REPEAT_PREDICATES.get(contract.repeat_until)
    if predicate is None:
        return False
    return not predicate(state_raw)


def _compute_next_step(
    state_raw: dict[str, Any],
    just_completed_step_id: str,
    state_yaml_path: str,
) -> dict[str, Any] | None:
    """Return the next_step dict for state.yaml, or None if the phase is complete.

    ORC-63: the DAG-walk `readiness.next_ready_node` is the single next-step
    computation. This wrapper preserves the `repeat_until` re-emit semantics:
    when the just-completed step declares a `repeat_until` predicate that
    evaluates False, the same step is re-emitted instead of advancing.

    Call this AFTER the just-completed node's status has been flipped to
    `completed` in `state_raw` so the DAG-walk skips it.
    """
    phase = state_raw.get("phase", "")

    # ISSUE-16: if the just-completed step declares a repeat_until predicate
    # that is currently False, re-emit it instead of advancing.
    try:
        contract = load_contract_for_step(just_completed_step_id, state_yaml_path)
    except (FileNotFoundError, ContractError):
        contract = None
    if contract is not None and contract.repeat_until:
        predicate = REPEAT_PREDICATES.get(contract.repeat_until)
        if predicate is None:
            sys.stderr.write(
                f"[record] unknown repeat_until predicate "
                f"{contract.repeat_until!r} on {just_completed_step_id}; "
                f"treating as absent\n"
            )
        elif not predicate(state_raw):
            return {"phase": phase, "step_id": just_completed_step_id}

    # Normal advance: the first ready node in the DAG.
    state = _state_from_raw(state_raw)
    nxt = readiness.next_ready_node(state)
    if nxt is None:
        return None
    return {"phase": phase, "step_id": nxt}


def record(
    state_yaml_path: str, payload: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    """Apply a record operation to state.yaml. Returns (result, exit_code)."""
    required = {"step_id", "phase", "outputs"}
    missing = required - payload.keys()
    if missing:
        return (
            {"reason": "payload_missing_keys", "missing": sorted(missing)},
            3,
        )

    step_id = payload["step_id"]
    phase = payload["phase"]
    # status is optional; default 'completed' for backward compat (FR-2).
    status = payload.get("status", "completed")

    if status not in _STATUS_TO_STATE_STATUS:
        return (
            {
                "reason": "invalid_status",
                "status": status,
                "valid_statuses": sorted(_STATUS_TO_STATE_STATUS),
            },
            3,
        )

    outputs = _coerce_payload_outputs(payload.get("outputs"))

    # Load contract once; reused for expected_outputs validation and Check B
    # (agent guard + token check). ContractError treated as missing file —
    # fall back to no-contract behavior rather than blocking the record.
    try:
        contract = load_contract_for_step(step_id, state_yaml_path)
    except (FileNotFoundError, ContractError) as _e:
        sys.stderr.write(f"[record] contract load failed for {step_id}: {_e}\n")
        contract = None

    outputs = _supplement_legacy_outputs(outputs, payload, contract)
    outputs = _supplement_learn_result(outputs, payload, step_id, status)
    outputs = _supplement_backlog_tickets_synced(outputs, step_id, status)

    if contract is not None and status == "completed":
        # ORC-63 AC-10: a declared output is satisfied only when its key is
        # present, the value is non-null/non-empty, and (for a path-named
        # output) the file exists on disk.
        try:
            _check_state_raw = yaml.safe_load(Path(state_yaml_path).read_text()) or {}
        except (OSError, yaml.YAMLError):
            _check_state_raw = {}
        # ORC-76 T-18: pass contract.outputs (list[dict]) directly; typed entries
        # (with path) get file-existence checks; legacy entries (path: None or
        # bare strings in legacy_output_names) keep the value-presence check.
        missing_out = _check_declared_outputs(contract.outputs, outputs, _check_state_raw)
        if missing_out:
            return (
                {
                    "reason": "missing_outputs",
                    "step_id": step_id,
                    "missing_outputs": missing_out,
                },
                3,
            )
        verdict_err = _validate_phase_review_output(step_id, outputs)
        if verdict_err is not None:
            return verdict_err

    # Check B: usage required for agent steps on completion.
    # Root cause of ISSUE-10.1: empty usage means cost report is blank and
    # telemetry has no data for the step.
    # Completed agent steps (payload agent is not None) MUST have input_tokens > 0
    # OR output_tokens > 0 (shell driver supplies usage via usage_adapters).
    #
    # ORC-48: if the contract declares an agent but the payload omits 'agent',
    # reject early so the driver knows it must include the field.
    contract_agent = contract.agent if contract is not None else None
    if status == "completed" and contract_agent is not None:
        if "agent" not in payload:
            return (
                {
                    "reason": "payload_missing_agent_for_agent_step",
                    "step_id": step_id,
                    "expected_agent": contract_agent,
                    "hint": (
                        "step contract declares agent: %s but payload omitted "
                        "the 'agent' field. The driver must include agent and "
                        "usage (input_tokens/output_tokens) in the done payload."
                    ) % contract_agent,
                },
                3,
            )

    agent = payload.get("agent")
    payload_usage = payload.get("usage") or {}
    if status == "completed" and agent is not None:
        has_tokens = _usage_has_tokens(payload_usage)
        if not has_tokens and not os.environ.get("ORCHESTRATOR_SKIP_USAGE_CHECK"):
            return (
                {
                    "reason": "agent_step_missing_usage",
                    "step_id": step_id,
                    "agent": agent,
                    "hint": (
                        "agent steps must self-report usage in the done payload: "
                        "set usage.input_tokens / usage.output_tokens"
                    ),
                },
                3,
            )

    path = Path(state_yaml_path)

    # Check C: detect corrupted state.yaml before AND after write.
    # Root cause of ISSUE-7: hand-edits that corrupt YAML surface far downstream
    # (dispatch crashes on malformed YAML three calls later). Capture pre-write
    # bytes so we can restore the file if either the initial parse or post-write
    # parse fails.
    with open(path, "rb") as f:
        pre_write_bytes = f.read()

    try:
        state_raw = yaml.safe_load(pre_write_bytes.decode("utf-8")) or {}
    except yaml.YAMLError as e:
        return (
            {
                "reason": "state_yaml_parse_failure",
                "detail": str(e),
                "hint": (
                    "state.yaml failed to parse before record. "
                    "Likely an earlier hand-edit corrupted the file."
                ),
            },
            4,
        )

    # Determine attempt number for this (phase, step_id).
    # Exclude in_progress entries from the count — they are placeholders, not
    # completed attempts, and must not inflate the attempt number (T-11 / FR-6).
    history = list(state_raw.get("step_history") or [])
    prior_attempts = [
        e.get("attempt") for e in history
        if isinstance(e, dict)
        and e.get("phase") == phase
        and e.get("step_id") == step_id
        and e.get("attempt")
        and e.get("status") != "in_progress"
    ]
    attempt = (max(prior_attempts) + 1) if prior_attempts else 1

    now = _utcnow_iso()

    # ISSUE-17: compute cost_usd live when absent from the payload.
    # Work on a local copy so we never mutate the caller's dict.
    usage: dict[str, Any] = dict(payload.get("usage") or {})
    agent_id = _resolve_agent_id(payload)
    if agent_id:
        usage["agent_id"] = agent_id

    if not usage.get("cost_usd"):
        resolved_model, computed_cost = _compute_cost_usd(agent, usage)
        if resolved_model is not None and computed_cost is not None:
            usage["model"] = resolved_model
            usage["cost_usd"] = computed_cost

    entry: dict[str, Any] = {
        "step_id": step_id,
        "phase": phase,
        "status": status,
        "agent": payload.get("agent"),
        "attempt": payload.get("attempt", attempt),
        "started_at": payload.get("started_at", now),
        "ended_at": now,
        "usage": usage,
        "evidence": _merge_evidence_block(outputs, payload.get("evidence")),
    }
    for key in _OPTIONAL_STEP_HISTORY_KEYS:
        if key in payload:
            entry[key] = payload[key]
    state_patch = payload.get("state_patch")
    if isinstance(state_patch, dict):
        _apply_state_patch(state_raw, state_patch)
    # BEFORE appending, strip any in_progress placeholder for this (step_id, phase).
    # The terminal record supersedes the in_progress entry; keeping both would
    # cause duplicate entries and break invariant checks (T-11 / FR-6, AC-3).
    history[:] = [
        h for h in history
        if not (
            h.get("status") == "in_progress"
            and h.get("step_id") == step_id
            and h.get("phase") == phase
        )
    ]
    history.append(entry)
    state_raw["step_history"] = history

    # FR-2: terminal halt statuses → set state.yaml.status per _STATUS_TO_STATE_STATUS.
    new_state_status = _STATUS_TO_STATE_STATUS.get(status)
    if new_state_status:
        state_raw["status"] = new_state_status

    # ORC-63: flip the node's status in workflow_plan via the shared mutator.
    # A repeat_until step whose predicate is still False stays `in_progress`
    # (re-dispatchable) — flipping it to `completed` would make the DAG-walk
    # skip its re-run. Otherwise a completed/recovered record marks the node
    # `completed`.
    #
    # orc-67: a run-phase-review completion with a needs_work/incomplete_phase
    # verdict opens a rework loop. `_rework_loop_active` decides:
    #   "retry"    — leave run-phase-review in_progress (pending) so the
    #                DAG-walk re-emits it after fix task-nodes complete.
    #                ORC-65: fix tasks are injected by the agent calling
    #                `orchestrator expand-plan` before COMPLETION — no
    #                per-step node reset needed here.
    #                Intermediate nodes (e.g. run-ux-critique) are untouched.
    #   "escalate" — retries exhausted: mark the node completed, downgrade this
    #                step_history entry to `blocked` (so the next `orchestrator
    #                next` exits 2 and the driver halts) and pause the workflow.
    if status in ("completed", "recovered", "abandoned", "failed"):
        if status in ("completed", "recovered") and _repeat_until_pending(step_id, state_yaml_path, state_raw):
            # repeat_until predicate not yet satisfied — keep node in_progress for re-dispatch.
            readiness.mark_node_status(state_raw, phase, step_id, "in_progress")
        else:
            routing = _resolve_routing(step_id, status, state_raw, phase)
            if routing == _HALT_CAP_EXCEEDED:
                # Retry cap exhausted — rewrite entry to blocked so dispatch exits 2.
                readiness.mark_node_status(state_raw, phase, step_id, "completed")
                entry["status"] = "blocked"
                state_raw["status"] = "blocked"
            elif routing == _HALT_KEYWORD:
                # No on_failure edge or explicit halt — preserve original entry status.
                readiness.mark_node_status(state_raw, phase, step_id, "completed")
                state_raw["status"] = "blocked"
            elif routing == "advance":
                readiness.mark_node_status(state_raw, phase, step_id, "completed")
            else:
                # routing is a target step_id — loop back: mark self completed,
                # reset target so next_ready_node() picks it up even if step_history
                # has a prior completed entry for it ("reset" beats history inference).
                readiness.mark_node_status(state_raw, phase, step_id, "completed")
                readiness.mark_node_status(state_raw, phase, routing, "reset")

    # Advance next_step (DAG-walk over the just-mutated node statuses).
    next_step = _compute_next_step(state_raw, step_id, state_yaml_path)
    if next_step:
        state_raw["next_step"] = next_step

    # Accumulate workflow_issues from this step's payload into state.yaml.
    # Dedup by dedup_key when present; stamp surfaced_at from phase/step_id.
    _incoming_issues = payload.get("workflow_issues")
    if isinstance(_incoming_issues, list) and _incoming_issues:
        _existing = state_raw.get("workflow_issues")
        if not isinstance(_existing, list):
            _existing = []
        _seen_dedup_keys = {
            i.get("dedup_key") for i in _existing if isinstance(i, dict) and i.get("dedup_key")
        }
        for _issue in _incoming_issues:
            if not isinstance(_issue, dict):
                continue
            _dk = (_issue.get("dedup_key") or "").strip()
            if _dk and _dk in _seen_dedup_keys:
                continue
            _issue.setdefault("surfaced_at", f"{phase}/{step_id}")
            _existing.append(_issue)
            if _dk:
                _seen_dedup_keys.add(_dk)
        state_raw["workflow_issues"] = _existing

    with open(path, "w") as f:
        yaml.safe_dump(state_raw, f, sort_keys=False, default_flow_style=False)

    # Check C (post-write): verify the written file is valid YAML.
    # If serialization produced invalid content, restore the pre-write bytes.
    try:
        with open(path) as f:
            yaml.safe_load(f)
    except yaml.YAMLError as e:
        with open(path, "wb") as f:
            f.write(pre_write_bytes)
        return (
            {
                "reason": "state_yaml_parse_failure",
                "detail": str(e),
                "hint": (
                    "state.yaml parse failed after record. "
                    "File has been restored to pre-write state. "
                    "Likely an earlier hand-edit corrupted the file."
                ),
            },
            4,
        )

    # Workflow-issue retro: if the payload carries workflow_issues, append
    # each one to spec/changes/<change_id>/retro.md. Best-effort — any
    # failure here never blocks the record.
    _retro_appended = 0
    issues = payload.get("workflow_issues")
    if isinstance(issues, list) and issues:
        worktree = state_raw.get("worktree_path") or state_raw.get("repo_root") or ""
        change_id = state_raw.get("change_id") or state_raw.get("slug") or ""
        if worktree and change_id:
            try:
                import os as _os
                import subprocess as _sp
                script = _resolve_append_retro_script(str(state_raw.get("repo_root") or ""))
                if script:
                    for issue in issues:
                        # per-issue phase/step fallback
                        issue.setdefault("surfaced_at", f"{phase}/{step_id}")
                    env = {
                        **_os.environ,
                        "WORKTREE_PATH": _os.path.expanduser(str(worktree)),
                        "CHANGE_ID": str(change_id),
                        "ISSUES_JSON": json.dumps(issues),
                    }
                    result = _sp.run(["bash", script], env=env, capture_output=True, text=True)
                    if result.returncode == 0 and result.stdout.strip():
                        try:
                            _retro_appended = int(json.loads(result.stdout.strip()).get("appended", 0))
                        except Exception:
                            _retro_appended = 0
            except Exception as exc:  # noqa: BLE001 — retro logging is best-effort
                sys.stderr.write(f"[record] retro append failed: {exc}\n")

    response: dict[str, Any] = {
        "step_id": step_id,
        "attempt": entry["attempt"],
        "next_step": next_step,
    }
    _issues_recorded = len(state_raw.get("workflow_issues") or [])
    if _issues_recorded:
        response["issues_recorded"] = _issues_recorded
    return (response, 0)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: orchestrator done <state.yaml>  (JSON payload on stdin)",
              file=sys.stderr)
        return 3
    state_yaml_path = argv[1]
    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError as exc:
        print(f"error: invalid JSON payload — {exc}", file=sys.stderr)
        return 3

    result, code = record(state_yaml_path, payload)
    print(json.dumps(result, sort_keys=True, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
