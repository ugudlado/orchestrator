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

_PHASE_REVIEW_VERDICTS = frozenset({"pass", "needs_work", "incomplete_phase"})


# Task tool result text embeds the subagent JSONL stem on its own line.
_AGENT_ID_FROM_TASK_RESULT_RE = re.compile(r"agentId:\s*([a-f0-9]{17})")


def _extract_agent_id_from_task_result(text: str | None) -> str | None:
    """Parse agentId from raw Task tool result text (transient done payload field)."""
    if not text:
        return None
    match = _AGENT_ID_FROM_TASK_RESULT_RE.search(text)
    return match.group(1) if match else None


def _resolve_agent_id(payload: dict[str, Any]) -> str | None:
    """agent_id from explicit payload fields or agent_task_result text."""
    usage = payload.get("usage") or {}
    return (
        payload.get("agent_id")
        or usage.get("agent_id")
        or _extract_agent_id_from_task_result(payload.get("agent_task_result"))
    )


def _payload_uses_chat_driver_jsonl(payload: dict[str, Any]) -> bool:
    """True when the done payload carries chat-driver (Task tool) JSONL signals."""
    if payload.get("agent_task_result"):
        return True
    if payload.get("agent_id"):
        return True
    usage = payload.get("usage") or {}
    return bool(usage.get("agent_id"))



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
    "abandoned": "blocked",
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
# Driver session resolution helpers (FR-6)
# ---------------------------------------------------------------------------

def _resolve_driver_session(state: dict, change_id: str) -> dict:
    """Resolve the driver session and return its usage dict.

    Resolution order:
      1. $ORCHESTRATOR_DRIVER_SESSION_ID env var.
      2. Scan ~/.claude/projects/<repo-slug>/ for most recent *.jsonl by mtime.

    Raises RuntimeError if session_id cannot be resolved.
    """
    from orchestrator_next.jsonl_usage import extract_driver_usage

    repo_root = state.get("repo_root") or ""

    session_id = os.environ.get("ORCHESTRATOR_DRIVER_SESSION_ID") or ""

    if not session_id:
        from pathlib import Path as _Path
        from orchestrator_next.jsonl_usage import _repo_slug

        slug_dir = _Path.home() / ".claude" / "projects" / _repo_slug(repo_root)
        if slug_dir.exists():
            jsonl_files = list(slug_dir.glob("*.jsonl"))
            if jsonl_files:
                newest = max(jsonl_files, key=lambda p: p.stat().st_mtime)
                session_id = newest.stem

    if not session_id:
        raise RuntimeError(
            f"[done] driver session_id not resolvable for change_id={change_id!r}: "
            f"set $ORCHESTRATOR_DRIVER_SESSION_ID or ensure a JSONL exists under "
            f"~/.claude/projects/<repo-slug>/"
        )

    usage = extract_driver_usage(repo_root, session_id)
    if not usage:
        usage = {}

    input_tok = usage.get("input_tokens") or 0
    output_tok = usage.get("output_tokens") or 0
    return {
        "session_id": session_id,
        "model": usage.get("model"),
        "total_tokens": input_tok + output_tok,
        "input_tokens": input_tok,
        "output_tokens": output_tok,
        "cache_read_input_tokens": usage.get("cache_read_input_tokens") or 0,
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens") or 0,
        "cost_usd": usage.get("cost_usd"),
        "started_at": None,
        "ended_at": None,
    }


from orchestrator_next.pricing import _compute_cost_usd  # noqa: E402


def _utcnow_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------


def compute_task_counts(tasks_yaml_path: "Path | None") -> dict:
    """Derive task counts from tasks.yaml status fields.

    Reads tasks.yaml directly — the single authority on task completion since
    implement-tasks writes status: completed per task after each commit.
    Fix tasks have ids starting with 'fix-'.

    Returns a dict with:
        tasks_total     — total number of tasks in tasks.yaml
        tasks_planned   — tasks whose id does not start with 'fix-'
        tasks_added     — tasks whose id starts with 'fix-' (rework additions)
        tasks_completed — tasks with status: completed
        tasks_failed    — tasks neither completed nor skipped
        resolve_rate    — tasks_completed / tasks_total (0.0 when total is 0)

    Returns None values when tasks.yaml is absent or has no tasks (spike path).
    """
    import yaml as _yaml

    _null = {
        "tasks_total": None,
        "tasks_planned": None,
        "tasks_added": None,
        "tasks_completed": None,
        "tasks_failed": None,
        "resolve_rate": None,
    }

    if tasks_yaml_path is None or not tasks_yaml_path.is_file():
        return _null

    try:
        doc = _yaml.safe_load(tasks_yaml_path.read_text(encoding="utf-8")) or {}
    except (_yaml.YAMLError, OSError):
        return _null

    tasks = doc.get("tasks") if isinstance(doc, dict) else None
    if not isinstance(tasks, list) or not tasks:
        return _null

    total = len(tasks)
    fix_count = sum(1 for t in tasks if str(t.get("id", "")).startswith("fix-"))
    planned = total - fix_count
    completed = sum(1 for t in tasks if t.get("status") == "completed")
    failed = total - completed
    resolve_rate = completed / total if total > 0 else 0.0

    return {
        "tasks_total": total,
        "tasks_planned": planned,
        "tasks_added": fix_count,
        "tasks_completed": completed,
        "tasks_failed": failed,
        "resolve_rate": round(resolve_rate, 6),
    }


def compute_retries(state: dict) -> dict:
    """Sum retries.* keys and extract human_interventions.

    Returns:
        retries_total, human_interventions
    """
    retries_section = state.get("retries") or {}
    if isinstance(retries_section, dict):
        retries_total = sum(
            v for v in retries_section.values() if isinstance(v, (int, float))
        )
    else:
        retries_total = 0

    human_interventions = state.get("human_interventions") or 0
    return {
        "retries_total": int(retries_total),
        "human_interventions": int(human_interventions),
    }


def compute_resolution(
    tasks_total,
    tasks_completed,
    retries_total: int,
    step_history: list,
    quarantine_events,
) -> dict:
    """Derive pass_at_1, pass_at_2, regressions, regression_rate.

    Approximation note: state.yaml retries are keyed by step_id (e.g.
    "run-phase-review"), not by task_id — so per-task attempt granularity
    is unavailable. We use:
      pass_at_1 = max(0, tasks_total - retries_total) / tasks_total
      pass_at_2 = tasks_completed / tasks_total
    This satisfies the monotonicity invariant pass_at_2 >= pass_at_1 and
    is the tightest approximation possible without per-task retry records.
    quarantine_events would normally reduce the numerator, but since
    quarantined tasks are not counted in tasks_completed either, the formula
    stays consistent.

    Returns all-None when tasks_total is None or zero (spike path).
    """
    if not tasks_total:
        return {
            "pass_at_1": None,
            "pass_at_2": None,
            "regressions": None,
            "regression_rate": None,
        }

    tc = tasks_completed if isinstance(tasks_completed, int) else 0

    pass_at_1 = round(max(0, tasks_total - retries_total) / tasks_total, 6)
    pass_at_2 = round(tc / tasks_total, 6)

    regressions = sum(
        1 for e in step_history
        if isinstance(e, dict) and e.get("regression")
    )
    regression_rate = round(regressions / tasks_total, 6)

    return {
        "pass_at_1": pass_at_1,
        "pass_at_2": pass_at_2,
        "regressions": regressions,
        "regression_rate": regression_rate,
    }


def run_git_churn(worktree: str, change_id: str) -> dict:
    """Count files_changed, insertions, deletions, total_commits, rework_commits.

    Searches git log for commits whose message contains the change_id or
    feature/<change_id> branch name. Falls back to zeros on any git failure.
    """
    import subprocess as _subprocess
    defaults: dict = {
        "files_changed": 0,
        "insertions": 0,
        "deletions": 0,
        "total_commits": 0,
        "rework_commits": 0,
        "rework_rate": 0.0,
    }
    try:
        result = _subprocess.run(
            ["git", "-C", worktree, "log",
             "--grep", change_id,
             "--no-merges",
             "--format=%H %s"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return defaults

        lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
        total_commits = len(lines)
        # Match legacy compute-swe-metrics.sh: grep -c "^fix:" behavior (NFR-3)
        rework_commits = sum(
            1 for ln in lines if re.match(r"^(fix|rework):", ln)
        )
        rework_rate = rework_commits / total_commits if total_commits > 0 else 0.0

        if not lines:
            return defaults

        # Get first and last commit SHA for diff range
        last_sha = lines[-1].split()[0]
        first_sha = lines[0].split()[0]

        # files_changed via --name-only diff
        name_result = _subprocess.run(
            ["git", "-C", worktree, "diff", "--name-only", f"{last_sha}^..{first_sha}"],
            capture_output=True, text=True, timeout=10,
        )
        files_changed = len([
            ln for ln in name_result.stdout.splitlines() if ln.strip()
        ]) if name_result.returncode == 0 else 0

        # insertions/deletions via --numstat
        num_result = _subprocess.run(
            ["git", "-C", worktree, "diff", "--numstat", f"{last_sha}^..{first_sha}"],
            capture_output=True, text=True, timeout=10,
        )
        insertions = 0
        deletions = 0
        if num_result.returncode == 0:
            for row in num_result.stdout.splitlines():
                parts = row.split()
                if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                    insertions += int(parts[0])
                    deletions += int(parts[1])

        return {
            "files_changed": files_changed,
            "insertions": insertions,
            "deletions": deletions,
            "total_commits": total_commits,
            "rework_commits": rework_commits,
            "rework_rate": round(rework_rate, 6),
        }
    except Exception:
        return defaults


def _phase_review_verdict(entry: dict) -> str | None:
    """Read verdict from step_history evidence.outputs.phase_review_report.

    Payload-time validation uses top-level ``outputs`` (_validate_phase_review_output);
    record nests those under ``evidence.outputs`` when appending step_history.
    """
    evidence = entry.get("evidence")
    if not isinstance(evidence, dict):
        return None
    outputs = evidence.get("outputs")
    if not isinstance(outputs, dict):
        return None
    report = outputs.get("phase_review_report")
    if isinstance(report, dict):
        verdict = report.get("verdict")
        return verdict if isinstance(verdict, str) else None
    return None


def extract_review_scores(state: dict) -> dict:
    """Extract review_score.overall from step_history entries.

    Only includes passing reviews (verdict ``pass``) and legacy entries that
    predate the verdict field. ``needs_work`` and ``incomplete_phase`` attempts
    are excluded so ``review_score_avg`` reflects achieved quality, not failed
    review rounds.

    Returns:
        scores_list (list of ints/floats), avg (float or None)
    """
    step_history = state.get("step_history") or []
    scores: list = []
    for entry in step_history:
        if not isinstance(entry, dict):
            continue
        verdict = _phase_review_verdict(entry)
        if verdict is not None and verdict != "pass":
            continue
        review_score = entry.get("review_score")
        if isinstance(review_score, dict):
            overall = review_score.get("overall")
            if overall is not None:
                try:
                    scores.append(float(overall))
                except (TypeError, ValueError):
                    pass

    avg = round(sum(scores) / len(scores), 4) if scores else None
    return {
        "scores_list": scores,
        "avg": avg,
    }


def wall_clock_minutes(state: dict):
    """Compute wall clock in minutes from state started_at and completed_at.

    Returns None if either timestamp is missing or unparseable.
    """
    started_at = state.get("started_at")
    completed_at = state.get("completed_at")
    if not started_at or not completed_at:
        return None

    def _parse_ts(ts):
        if isinstance(ts, _dt.datetime):
            if ts.tzinfo is None:
                return ts.replace(tzinfo=_dt.timezone.utc)
            return ts
        s = str(ts).strip()
        # Normalize space-separated UTC offset to ISO 8601
        s = s.replace(" ", "T")
        s = re.sub(r"\+00:00$", "Z", s)
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
            try:
                parsed = _dt.datetime.strptime(s.rstrip("Z"), fmt.rstrip("Z"))
                return parsed.replace(tzinfo=_dt.timezone.utc)
            except ValueError:
                continue
        return None

    start = _parse_ts(started_at)
    end = _parse_ts(completed_at)
    if start is None or end is None:
        return None
    delta = (end - start).total_seconds()
    return round(delta / 60.0, 4)


# ---------------------------------------------------------------------------
# Phase 5: _resolve_feature_metrics_tasks_path — tasks.md path resolver
# ---------------------------------------------------------------------------

def _resolve_workflow_artifact_path(state_raw: dict[str, Any], filename: str) -> Path | None:
    """Unified resolver for workflow artifact files (tasks.md, design.md, etc.).

    Resolution order:
      1. If filename == "tasks.md" and state_raw has an explicit ``tasks_path``
         override, return that path immediately.
      2. If ``worktree_path`` is set AND that directory exists on disk AND the
         candidate file itself exists, return
         ``<worktree_path>/spec/changes/<change_id>/<filename>``; otherwise fall
         through to priority 3.
      3. Else if ``repo_root`` is set, return
         ``<repo_root>/spec/changes/<change_id>/<filename>``.
      4. Return None when no candidate can be constructed at all.
    """
    # 1. Explicit tasks_path override (tasks.md only).
    if filename == "tasks.md":
        raw_path = state_raw.get("tasks_path")
        if isinstance(raw_path, str) and raw_path:
            return Path(os.path.expanduser(raw_path))

    change_id = state_raw.get("change_id")
    if not (isinstance(change_id, str) and change_id):
        return None

    # 2. Worktree path — only when the directory actually exists.
    worktree_path = state_raw.get("worktree_path")
    if isinstance(worktree_path, str) and worktree_path:
        wt = Path(os.path.expanduser(worktree_path))
        if wt.is_dir():
            candidate = wt / "spec" / "changes" / change_id / filename
            if candidate.is_file():
                return candidate

    # 3. Fall back to repo_root.
    repo_root = state_raw.get("repo_root")
    if isinstance(repo_root, str) and repo_root:
        return Path(os.path.expanduser(repo_root)) / "spec" / "changes" / change_id / filename

    return None


def _resolve_feature_metrics_tasks_path(state: dict) -> Path:
    """Thin wrapper: resolve tasks.md path for feature_metrics computation."""
    return _resolve_workflow_artifact_path(state, "tasks.md") or Path("")


# ---------------------------------------------------------------------------
# Feature metrics computation
# ---------------------------------------------------------------------------

def _resolve_feature_metrics(state: dict, change_id: str) -> dict:
    """Compute feature metrics from state and tasks.yaml. Pure — no DB writes.

    Raises RuntimeError if started_at/completed_at missing on feature/bugfix.
    """
    schema = str(state.get("schema") or "feature")
    worktree = str(state.get("worktree_path") or state.get("repo_root") or "")

    if schema in ("feature", "bugfix"):
        if not state.get("started_at") or not state.get("completed_at"):
            raise RuntimeError(
                f"_resolve_feature_metrics: state missing started_at/completed_at "
                f"for schema={schema}"
            )

    tasks_yaml = _resolve_workflow_artifact_path(state, "tasks.yaml")
    task_counts = compute_task_counts(tasks_yaml)

    retries = compute_retries(state)
    resolution = compute_resolution(
        tasks_total=task_counts.get("tasks_total"),
        tasks_completed=task_counts.get("tasks_completed"),
        retries_total=retries["retries_total"],
        step_history=state.get("step_history") or [],
        quarantine_events=state.get("quarantine_events"),
    )
    churn = run_git_churn(worktree, change_id)
    reviews = extract_review_scores(state)
    wc = wall_clock_minutes(state)

    return {
        "schema_name": schema,
        **task_counts,
        **retries,
        **resolution,
        **churn,
        "review_scores_json": json.dumps(reviews["scores_list"]),
        "review_score_avg": reviews["avg"],
        "wall_clock_minutes": wc,
        "source": f"done@{_utcnow_iso()}",
    }


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
    # Completed agent steps (payload agent is not None) MUST have input_tokens > 0 OR output_tokens > 0,
    # unless agent_task_result is present with a parseable agentId — record.py
    # then pulls billing-truth tokens from the subagent JSONL (driver does not
    # parse usage blocks). Explicit agent_id alone does not bypass this check.
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
                        "agent_task_result (raw Task tool result text) in the "
                        "done payload. See skills/orchestrate/SKILL.md."
                    ) % contract_agent,
                },
                3,
            )

    agent = payload.get("agent")
    payload_usage = payload.get("usage") or {}
    agent_task_result = payload.get("agent_task_result")
    resolved_agent_id = _resolve_agent_id(payload)
    if status == "completed" and agent is not None:
        has_tokens = _usage_has_tokens(payload_usage)
        if not has_tokens and not os.environ.get("ORCHESTRATOR_SKIP_USAGE_CHECK"):
            if agent_task_result and resolved_agent_id:
                pass  # subagent JSONL enrichment below supplies billing-truth usage
            elif agent_task_result:
                return (
                    {
                        "reason": "agent_step_missing_usage",
                        "step_id": step_id,
                        "agent": agent,
                        "hint": (
                            "agent_task_result present but no agentId: <17hex> line found; "
                            "cannot load subagent JSONL for usage"
                        ),
                    },
                    3,
                )
            else:
                return (
                    {
                        "reason": "agent_step_missing_usage",
                        "step_id": step_id,
                        "agent": agent,
                        "hint": (
                            "agent steps must self-report usage in the done payload: "
                            "set usage.input_tokens / usage.output_tokens, or pass "
                            "agent_task_result with an agentId line for subagent JSONL enrichment"
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

    # JSONL enrichment (chat-driver only): when agent_id is known (explicit or
    # parsed from agent_task_result), pull billing-truth usage from the subagent
    # JSONL. Shell-driver payloads lack agent_task_result/agent_id and skip this.
    # JSONL wins for input/output/cache_* and model; we keep caller-provided
    # tool_calls and duration_ms only if JSONL lacks them.
    agent_id = resolved_agent_id
    if agent_id and _payload_uses_chat_driver_jsonl(payload):
        try:
            from orchestrator_next.jsonl_usage import (
                extract_agent_usage,
                extract_tool_calls,
                locate_subagent_jsonl_path,
            )
            repo_root = state_raw.get("repo_root") or ""
            jsonl_usage = extract_agent_usage(repo_root, agent_id)
            for key in (
                "input_tokens",
                "output_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
                "model",
                "turns",
            ):
                if jsonl_usage.get(key) is not None:
                    usage[key] = jsonl_usage[key]
            # duration_ms / tool_calls only if caller didn't supply them.
            if usage.get("duration_ms") is None and jsonl_usage.get("duration_ms") is not None:
                usage["duration_ms"] = jsonl_usage["duration_ms"]
            if not usage.get("tool_calls") and jsonl_usage.get("tool_calls"):
                usage["tool_calls"] = jsonl_usage["tool_calls"]
            jsonl_path = locate_subagent_jsonl_path(repo_root, agent_id)
            if jsonl_path is not None:
                detail = extract_tool_calls(jsonl_path)
                if detail:
                    usage["tool_calls_detail"] = detail
            usage["agent_id"] = agent_id
        except Exception as exc:  # noqa: BLE001 — enrichment is best-effort
            sys.stderr.write(f"[record] jsonl enrichment failed for agent_id={agent_id}: {exc}\n")

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
                # reset target to pending so next_ready_node() picks it up.
                readiness.mark_node_status(state_raw, phase, step_id, "completed")
                readiness.mark_node_status(state_raw, phase, routing, "pending")

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
