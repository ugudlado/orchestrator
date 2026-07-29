"""orchestrator record subcommand.

Accepts a completed step's outputs + usage + evidence via stdin JSON,
validates against the contract's `expected_outputs`, writes a terminal
`step_history` entry with uniform `started_at` / `completed_at` / `usage`,
and advances `next_step` per `workflow_plan`.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from orchestrator_next.parser import AgentStepContract, ContractError, State, compute_attempt, load_contract_for_step, safe_write_yaml as _safe_write_yaml_base
from orchestrator_next import readiness
from orchestrator_next.pricing import _compute_cost_usd


class _RecordError(Exception):
    """Internal: raised by validation helpers, caught in record()."""

    def __init__(self, reason: dict[str, Any], code: int) -> None:
        self.reason = reason
        self.code = code


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


_STATE_PATCH_KEYS = frozenset({
    "retries",
    "quarantine_events",
    "baseline",
    "refresh_artifacts",
    "worktree_path",
    "branch",
})

_PHASE_REVIEW_VERDICTS = frozenset({"pass", "needs_work", "incomplete_phase"})
_SUCCESS_STATUSES = frozenset({"completed", "recovered"})


def _usage_has_tokens(usage: dict[str, Any]) -> bool:
    return (
        (isinstance(usage.get("input_tokens"), (int, float)) and usage["input_tokens"] > 0)
        or (isinstance(usage.get("output_tokens"), (int, float)) and usage["output_tokens"] > 0)
    )


def _validate_phase_review_output(step_id: str, outputs: dict[str, Any]) -> None:
    """Reject invalid phase_review_report.verdict at the record boundary.

    Raises _RecordError on invalid input; returns None on success.
    """
    if step_id != "review":
        return
    report = outputs.get("phase_review_report")
    if not isinstance(report, dict):
        raise _RecordError(
            {
                "reason": "invalid_phase_review_report",
                "step_id": step_id,
                "hint": "outputs.phase_review_report must be an object with verdict",
            },
            3,
        )
    verdict = report.get("verdict")
    if not isinstance(verdict, str) or verdict not in _PHASE_REVIEW_VERDICTS:
        raise _RecordError(
            {
                "reason": "invalid_phase_review_verdict",
                "step_id": step_id,
                "verdict": verdict,
                "valid_verdicts": sorted(_PHASE_REVIEW_VERDICTS),
            },
            3,
        )
    # Caller (_validate_outputs) guarantees status == "completed"; a non-pass
    # verdict must ship as status: failed or the on_failure edge never fires
    # (live bypass: BKG-575 advanced to ticket-qa with a needs_work review).
    if verdict != "pass":
        raise _RecordError(
            {
                "reason": "invalid_phase_review_status_for_verdict",
                "step_id": step_id,
                "verdict": verdict,
                "hint": (
                    "status: completed requires phase_review_report.verdict: pass; "
                    "emit status: failed for needs_work/incomplete_phase"
                ),
            },
            3,
        )


def _normalize_review_payload_status(
    step_id: str, status: str, outputs: dict[str, Any]
) -> str:
    """Coerce agent mistakes before routing.

    design-review agents sometimes emit ``status: completed`` with
    ``design_review_result: needs_work``. Routing keys off ``status``, not the
    output field — normalize to ``failed`` so the workflow's ``on_failure`` edge
    fires.
    """
    if step_id == "review":
        report = outputs.get("phase_review_report")
        verdict = report.get("verdict") if isinstance(report, dict) else None
        if verdict in ("needs_work", "incomplete_phase") and status in _SUCCESS_STATUSES:
            sys.stderr.write(
                "[record] review: coercing status "
                f"{status!r} → 'failed' (phase_review_report.verdict: {verdict})\n"
            )
            return "failed"
        return status
    if step_id != "design-review":
        return status
    result = outputs.get("design_review_result")
    if result == "needs_work" and status in _SUCCESS_STATUSES:
        sys.stderr.write(
            "[record] design-review: coercing status "
            f"{status!r} → 'failed' (design_review_result: needs_work)\n"
        )
        return "failed"
    return status


def _validate_design_review_output(step_id: str, outputs: dict[str, Any]) -> None:
    """Reject completed design-review payloads that are not a pass.

    Caller (_validate_outputs) guarantees status == "completed".
    """
    if step_id != "design-review":
        return
    result = outputs.get("design_review_result")
    if result != "pass":
        raise _RecordError(
            {
                "reason": "invalid_design_review_result",
                "step_id": step_id,
                "design_review_result": result,
                "hint": (
                    "status: completed requires design_review_result: pass; "
                    "emit status: failed for needs_work"
                ),
            },
            3,
        )


# ---------------------------------------------------------------------------
# review needs_work rework loop
# ---------------------------------------------------------------------------

_DEFAULT_MAX_RETRY_ROUNDS = 3

# Map every payload `status` the driver may send to the state.yaml.status it
# implies (None = no state-level change). Single source of truth for both
# validation and halt semantics — keeping them in one dict prevents the
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
    for key in _STATE_PATCH_KEYS - {"retries", "quarantine_events"}:
        if key in patch:
            state_raw[key] = patch[key]


# ---------------------------------------------------------------------------
# Statechart routing (on_success / on_failure edges)
# ---------------------------------------------------------------------------

_HALT_KEYWORD = "halt"


def _find_workflow_node(state_raw: dict[str, Any], phase: str, step_id: str) -> dict[str, Any] | None:
    """Return the node dict for step_id in workflow_plan[phase].nodes, or None."""
    phase_block = (state_raw.get("workflow_plan") or {}).get(phase)
    if not isinstance(phase_block, dict):
        return None
    nodes = phase_block.get("nodes") or []
    return readiness.find_node(nodes, step_id)


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
        max_r = int((node or {}).get("max_retries") or _DEFAULT_MAX_RETRY_ROUNDS)
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


def _utcnow_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _state_from_raw(state_raw: dict[str, Any]) -> State:
    """Build an in-memory State view over a mutated `state_raw` dict.

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


def _validate_shape(payload: dict[str, Any]) -> tuple[str, str, str]:
    """Check required keys and status value. Returns (step_id, phase, status)."""
    missing = {"step_id", "phase", "outputs"} - payload.keys()
    if missing:
        raise _RecordError(
            {"reason": "payload_missing_keys", "missing": sorted(missing)},
            3,
        )
    step_id = payload["step_id"]
    phase = payload["phase"]
    status = payload.get("status", "completed")
    if status not in _STATUS_TO_STATE_STATUS:
        raise _RecordError(
            {
                "reason": "invalid_status",
                "status": status,
                "valid_statuses": sorted(_STATUS_TO_STATE_STATUS),
            },
            3,
        )
    return step_id, phase, status


def _load_contract(step_id: str) -> Any:
    """Load a step contract, degrading to None on any missing/malformed file."""
    try:
        return load_contract_for_step(step_id)
    except (FileNotFoundError, ContractError) as _e:
        sys.stderr.write(f"[record] contract load failed for {step_id}: {_e}\n")
        return None


def _apply_default_outputs(
    outputs: dict[str, Any],
    contract: Any,
    status: str,
) -> dict[str, Any]:
    """Fill in any missing outputs from contract.default_outputs on completed steps."""
    if status != "completed":
        return outputs
    defaults = getattr(contract, "default_outputs", None) or {}
    if not defaults:
        return outputs
    out = dict(outputs)
    for key, value in defaults.items():
        if key not in out or out[key] is None or (hasattr(out[key], "__len__") and len(out[key]) == 0):
            out[key] = value
            sys.stderr.write(
                f"[record] supplemented outputs.{key} from contract default "
                f"(step omitted it)\n"
            )
    return out


def _validate_outputs(step_id: str, status: str, outputs: dict[str, Any]) -> None:
    if status != "completed":
        return
    _validate_phase_review_output(step_id, outputs)
    _validate_design_review_output(step_id, outputs)


def _validate_agent_usage(
    payload: dict[str, Any], step_id: str, status: str, contract: Any,
) -> str | None:
    """Check agent field presence and token guard. Returns agent name or None."""
    contract_model = contract.model if isinstance(contract, AgentStepContract) else None
    if status == "completed" and contract_model is not None and "agent" not in payload:
        raise _RecordError(
            {
                "reason": "payload_missing_agent_for_agent_step",
                "step_id": step_id,
                "expected_model": contract_model,
                "hint": (
                    "step contract declares model: %s but payload omitted "
                    "the 'agent' field. The driver must include agent and "
                    "usage (input_tokens/output_tokens) in the done payload."
                ) % contract_model,
            },
            3,
        )
    agent = payload.get("agent")
    if status == "completed" and agent is not None:
        if not _usage_has_tokens(payload.get("usage") or {}) and not os.environ.get("ORCHESTRATOR_SKIP_USAGE_CHECK"):
            raise _RecordError(
                {
                    "reason": "agent_step_missing_usage",
                    "step_id": step_id,
                    "agent": agent,
                    "hint": "agent steps must self-report usage in the done payload: set usage.input_tokens / usage.output_tokens",
                },
                3,
            )
    return agent


def _validate_payload(
    payload: dict[str, Any],
) -> tuple[str, str, str, dict[str, Any], Any, Any]:
    """Validate a done-payload end-to-end. Returns (step_id, phase, status, outputs, contract, agent)."""
    step_id, phase, status = _validate_shape(payload)
    outputs = _coerce_payload_outputs(payload.get("outputs"))
    status = _normalize_review_payload_status(step_id, status, outputs)
    contract = _load_contract(step_id)
    outputs = _apply_default_outputs(outputs, contract, status)
    _validate_outputs(step_id, status, outputs)
    agent = _validate_agent_usage(payload, step_id, status, contract)
    return step_id, phase, status, outputs, contract, agent


def _load_state_safe(path: Path) -> tuple[dict[str, Any], bytes]:
    """Read state.yaml safely.

    Returns (state_raw, pre_write_bytes) on success.
    Raises _RecordError on YAMLError.
    """
    # Detect corrupted state.yaml before AND after write.
    # Hand-edits that corrupt YAML surface far downstream (dispatch crashes on
    # malformed YAML three calls later). Capture pre-write bytes so we can
    # restore the file if either the initial parse or post-write parse fails.
    with open(path, "rb") as f:
        pre_write_bytes = f.read()

    try:
        state_raw = yaml.safe_load(pre_write_bytes.decode("utf-8")) or {}
    except yaml.YAMLError as e:
        raise _RecordError(
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

    return (state_raw, pre_write_bytes)


def _build_history_entry(
    payload: dict[str, Any],
    step_id: str,
    phase: str,
    status: str,
    outputs: dict[str, Any],
    agent: Any,
    state_raw: dict[str, Any],
) -> dict[str, Any]:
    """Compute attempt number and build the step_history entry dict."""
    history = list(state_raw.get("step_history") or [])
    attempt = compute_attempt(history, phase, step_id, include_in_progress=False)

    now = _utcnow_iso()

    # Compute cost_usd live when absent from the payload.
    # Work on a local copy so we never mutate the caller's dict.
    usage: dict[str, Any] = dict(payload.get("usage") or {})
    agent_id = payload.get("agent_id") or usage.get("agent_id")
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
    # Derive duration_ms from wall-clock timestamps when the payload omitted it
    # (script steps never self-report duration). Unparseable stamps → skip.
    if "duration_ms" not in usage:
        try:
            started_dt = _dt.datetime.fromisoformat(
                str(entry["started_at"]).replace("Z", "+00:00")
            )
            ended_dt = _dt.datetime.fromisoformat(
                str(entry["ended_at"]).replace("Z", "+00:00")
            )
            usage["duration_ms"] = int((ended_dt - started_dt).total_seconds() * 1000)
        except (TypeError, ValueError):
            pass
    entry["outputs"] = dict(outputs)
    return entry


def _apply_routing(
    entry: dict[str, Any],
    step_id: str,
    phase: str,
    status: str,
    state_raw: dict[str, Any],
) -> None:
    """Set state_raw["status"] and flip workflow_plan node statuses per routing logic."""
    new_state_status = _STATUS_TO_STATE_STATUS.get(status)
    if new_state_status:
        state_raw["status"] = new_state_status

    if status in ("completed", "recovered", "abandoned", "failed"):
        routing = _resolve_routing(step_id, status, state_raw, phase)
        if routing == _HALT_CAP_EXCEEDED:
            # Retry cap exhausted — rewrite entry to blocked so dispatch exits 2.
            readiness.mark_node_status(state_raw, phase, step_id, "completed")
            entry["status"] = "blocked"
            state_raw["status"] = "blocked"
        elif routing == _HALT_KEYWORD:
            # No on_failure edge or explicit halt. A *failed* deterministic script
            # (load-ticket-context, create-worktree, ...) must NOT read as completed:
            # a resume would then treat its dependents as ready and run them without
            # the missing artifact. But `abandoned` must terminate as completed to
            # stop infinite re-dispatch (ORC-75) — only `failed` flips the node.
            node_status = "failed" if status == "failed" else "completed"
            readiness.mark_node_status(state_raw, phase, step_id, node_status)
            state_raw["status"] = "blocked"
        elif routing == "advance" or status in _SUCCESS_STATUSES:
            # "advance", or routing is an explicit on_success target step_id
            # (e.g. review -> ticket-qa) — the step genuinely passed,
            # so mark it completed; next_ready_node() picks up the target via
            # normal dependency satisfaction. Nothing needs to be reset.
            readiness.mark_node_status(state_raw, phase, step_id, "completed")
        else:
            # routing is an on_failure target step_id — loop back: reset both
            # the failing gate and its fixer so next_ready_node() re-runs the
            # fixer, then re-verifies through the gate once the fixer
            # completes. Marking the gate "completed" here would make it
            # terminal (downstream deps ready) even though it failed — the fix
            # would ship unverified and max_retries on the gate would never
            # advance past 1.
            readiness.mark_node_status(state_raw, phase, step_id, "reset")
            readiness.mark_node_status(state_raw, phase, routing, "reset")


def _accumulate_issues(
    state_raw: dict[str, Any],
    phase: str,
    step_id: str,
    payload: dict[str, Any],
) -> None:
    """Merge payload["workflow_issues"] into state_raw["workflow_issues"], deduped by dedup_key."""
    # Accumulate workflow_issues from this step's payload into state.yaml.
    # Dedup by dedup_key when present; stamp surfaced_at from phase/step_id.
    incoming_issues = payload.get("workflow_issues")
    if isinstance(incoming_issues, list) and incoming_issues:
        existing = state_raw.get("workflow_issues")
        if not isinstance(existing, list):
            existing = []
        seen_dedup_keys = {
            i.get("dedup_key") for i in existing if isinstance(i, dict) and i.get("dedup_key")
        }
        for issue in incoming_issues:
            if not isinstance(issue, dict):
                continue
            dk = (issue.get("dedup_key") or "").strip()
            if dk and dk in seen_dedup_keys:
                continue
            issue.setdefault("surfaced_at", f"{phase}/{step_id}")
            existing.append(issue)
            if dk:
                seen_dedup_keys.add(dk)
        state_raw["workflow_issues"] = existing


def _safe_write_yaml(path: Path, state_raw: dict[str, Any], pre_write_bytes: bytes) -> None:
    """Write YAML and validate post-write parse, restoring bytes and raising _RecordError on failure."""
    try:
        _safe_write_yaml_base(path, state_raw, pre_write_bytes)
    except yaml.YAMLError as e:
        raise _RecordError(
            {
                "reason": "state_yaml_parse_failure",
                "detail": str(e),
                "hint": "state.yaml parse failed after record. File has been restored to pre-write state.",
            },
            4,
        ) from e


# ---------------------------------------------------------------------------
# Headless durability — auto-commit state so ephemeral runs can resume
# ---------------------------------------------------------------------------

def _headless() -> bool:
    """Headless = nobody at a terminal to keep state alive (CI job, cloud
    sandbox). Opt-in via ORCHESTRATOR_HEADLESS=1; cloud sessions already
    carry CLAUDE_CODE_REMOTE=true."""
    return (
        os.environ.get("ORCHESTRATOR_HEADLESS") == "1"
        or os.environ.get("CLAUDE_CODE_REMOTE") == "true"
    )


def autocommit_state(state_yaml_path: str, *, push: bool = False) -> None:
    """Commit the state dir on the current branch so an ephemeral headless run
    can resume after the filesystem is gone (see DRIVE.md "Durability").

    No-op unless headless. Best-effort — a git failure never fails the record.
    `add -f` because .orchestrator/ is gitignored (ephemeral locally, durable
    when headless). Pathspec commit so concurrently staged work is untouched.
    """
    if not _headless():
        return
    state_dir = str(Path(state_yaml_path).resolve().parent)
    slug = Path(state_dir).name

    # Ambient GIT_DIR/GIT_INDEX_FILE/GIT_WORK_TREE (e.g. leaked from a pre-commit
    # hook subprocess) override -C and redirect these calls at the wrong repo.
    git_env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}

    def _git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", state_dir, *args], capture_output=True, text=True, env=git_env
        )

    try:
        _git("add", "-f", "--", state_dir)
        if _git("diff", "--cached", "--quiet", "--", state_dir).returncode != 0:
            proc = _git("commit", "-m", f"wip: orchestrator state for {slug}",
                        "--", state_dir)
            if proc.returncode != 0:
                sys.stderr.write(
                    f"[record] state auto-commit failed: {proc.stderr.strip()}\n")
        if push:
            proc = _git("push", "origin", "HEAD")
            if proc.returncode != 0:
                sys.stderr.write(
                    f"[record] state auto-push failed: {proc.stderr.strip()}\n")
    except Exception as exc:  # noqa: BLE001 — durability is best-effort
        sys.stderr.write(f"[record] state auto-commit skipped: {exc}\n")


def record(
    state_yaml_path: str, payload: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    """Apply a record operation to state.yaml. Returns (result, exit_code)."""
    path = Path(state_yaml_path)
    try:
        state_raw, pre_write_bytes = _load_state_safe(path)
        step_id, phase, status, outputs, contract, agent = _validate_payload(payload)
    except _RecordError as e:
        return (e.reason, e.code)

    entry = _build_history_entry(payload, step_id, phase, status, outputs, agent, state_raw)

    state_patch = payload.get("state_patch")
    if isinstance(state_patch, dict):
        _apply_state_patch(state_raw, state_patch)

    history = [
        h for h in (state_raw.get("step_history") or [])
        if not (h.get("status") == "in_progress" and h.get("step_id") == step_id and h.get("phase") == phase)
    ]
    history.append(entry)
    state_raw["step_history"] = history

    _apply_routing(entry, step_id, phase, status, state_raw)
    state = _state_from_raw(state_raw)
    nxt = readiness.next_ready_node(state)
    next_step = {"phase": state_raw.get("phase", ""), "step_id": nxt} if nxt else None
    if next_step:
        state_raw["next_step"] = next_step

    _accumulate_issues(state_raw, phase, step_id, payload)

    try:
        _safe_write_yaml(path, state_raw, pre_write_bytes)
    except _RecordError as e:
        return (e.reason, e.code)

    # Headless: state changed on disk — commit it; push once when the run
    # transitions to blocked (that's the resume-later case, incl. the
    # self-drive `done` path which never reaches run_loop's exit handling).
    autocommit_state(state_yaml_path, push=state_raw.get("status") == "blocked")

    response: dict[str, Any] = {
        "step_id": step_id,
        "attempt": entry["attempt"],
        "next_step": next_step,
    }
    issues_recorded = len(state_raw.get("workflow_issues") or [])
    if issues_recorded:
        response["issues_recorded"] = issues_recorded
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
    # Surface the running cost total mid-run for standalone/self-driven callers
    # (DRIVE.md walks next/done itself). Re-derived from the just-written state.
    if code == 0:
        try:
            from orchestrator_next.pricing import format_cost_so_far
            with open(state_yaml_path) as f:
                _state = yaml.safe_load(f) or {}
            sys.stderr.write(format_cost_so_far(_state) + "\n")
        except (OSError, yaml.YAMLError):
            pass
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
