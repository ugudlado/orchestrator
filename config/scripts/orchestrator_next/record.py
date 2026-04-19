"""HL-287 M5: orchestrator record subcommand.

Accepts a completed step's outputs + usage + evidence via stdin JSON,
validates against the contract's `expected_outputs`, writes a terminal
`step_history` entry with uniform `started_at` / `completed_at` / `usage`,
and advances `next_step` per `workflow_plan`.
"""
from __future__ import annotations

import datetime as _dt
import functools
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from orchestrator_next.parser import ContractError, load_contract_for_step, load_state


# ---------------------------------------------------------------------------
# Cost computation helpers (ISSUE-17)
# ---------------------------------------------------------------------------

def _orchestrator_home() -> Path:
    """Return the orchestrator repo root.

    Prefers ORCHESTRATOR_HOME env var; falls back to the parent of the
    `config/scripts/` tree this module lives in.
    """
    env = os.environ.get("ORCHESTRATOR_HOME")
    if env:
        return Path(env)
    # __file__ is config/scripts/orchestrator_next/record.py
    return Path(__file__).parent.parent.parent.parent


@functools.lru_cache(maxsize=1)
def _load_routes() -> dict:
    """Load scripts/routes.yaml (cached per process)."""
    path = _orchestrator_home() / "scripts" / "routes.yaml"
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except (FileNotFoundError, OSError, yaml.YAMLError):
        return {}


@functools.lru_cache(maxsize=1)
def _load_pricing() -> dict:
    """Load config/pricing.yaml (cached per process)."""
    path = _orchestrator_home() / "config" / "pricing.yaml"
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except (FileNotFoundError, OSError, yaml.YAMLError):
        return {}


def _compute_cost_usd(agent: str, usage: dict) -> tuple[str | None, float | None]:
    """Resolve agent → model_id and compute cost from usage token counts.

    Resolution order:
      1. routes.agents[agent] → backend_name
      2a. routes.backends[backend_name] → model_id  (for native_* keys)
      2b. routes.models[backend_name].model → model_id  (for proxy models)
    Price lookup: pricing.models[model_id]; fallback: pricing.default.

    Returns (model_id, cost_usd) or (None, None) if resolution fails.
    Missing token fields default to 0.
    """
    routes = _load_routes()
    pricing = _load_pricing()

    # Step 1: agent → backend
    backend = (routes.get("agents") or {}).get(agent)
    if not backend:
        sys.stderr.write(
            f"[record] cost_usd: agent {agent!r} not found in routes.yaml agents; "
            f"skipping cost computation\n"
        )
        return None, None

    # Step 2: backend → model_id
    model_id: str | None = None
    backends_map = routes.get("backends") or {}
    if backend in backends_map:
        model_id = backends_map[backend]
    else:
        # Try routes.models.<backend>.model (proxy path)
        model_entry = (routes.get("models") or {}).get(backend)
        if isinstance(model_entry, dict):
            model_id = model_entry.get("model")
    if not model_id:
        sys.stderr.write(
            f"[record] cost_usd: backend {backend!r} for agent {agent!r} "
            f"not resolved to a model_id; skipping cost computation\n"
        )
        return None, None

    # Step 3: model_id → price block (fallback to default)
    price = (pricing.get("models") or {}).get(model_id)
    if price is None:
        price = pricing.get("default")
    if not isinstance(price, dict):
        sys.stderr.write(
            f"[record] cost_usd: no price entry for model {model_id!r} and no default; "
            f"skipping cost computation\n"
        )
        return None, None

    # Step 4: compute cost
    input_tokens = usage.get("input_tokens") or 0
    output_tokens = usage.get("output_tokens") or 0
    cache_read_tokens = usage.get("cache_read_input_tokens") or 0
    cost = (
        input_tokens * (price.get("input") or 0) / 1_000_000
        + output_tokens * (price.get("output") or 0) / 1_000_000
        + cache_read_tokens * (price.get("cache_read") or 0) / 1_000_000
    )
    return model_id, cost


def _utcnow_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_tasks_md(state_raw: dict[str, Any]) -> Path | None:
    """Resolve the tasks.md path from state.yaml fields.

    Preference: explicit `tasks_path`, else `<worktree_path>/spec/changes/<change_id>/tasks.md`.
    Returns None if neither shape can be constructed.
    """
    raw_path = state_raw.get("tasks_path")
    if isinstance(raw_path, str) and raw_path:
        return Path(os.path.expanduser(raw_path))

    worktree = state_raw.get("worktree_path")
    change_id = state_raw.get("change_id")
    if isinstance(worktree, str) and worktree and isinstance(change_id, str) and change_id:
        return Path(os.path.expanduser(worktree)) / "spec" / "changes" / change_id / "tasks.md"

    return None


def _check_all_tasks_completed(state_raw: dict[str, Any]) -> bool:
    """Return True iff no unchecked `- [ ]` items remain in tasks.md.

    Missing or unreadable tasks.md returns True (fail-open: advance).
    """
    path = _resolve_tasks_md(state_raw)
    if path is None:
        return True
    try:
        text = path.read_text()
    except (FileNotFoundError, OSError):
        return True
    return re.search(r"^\s*-\s*\[\s*\]", text, re.MULTILINE) is None


_REPEAT_PREDICATES = {
    "all_tasks_completed": _check_all_tasks_completed,
}


def _compute_next_step(
    state_raw: dict[str, Any],
    just_completed_step_id: str,
    state_yaml_path: str,
) -> dict[str, Any] | None:
    """Return the next_step dict for state.yaml, or None if phase complete."""
    phase = state_raw.get("phase", "")
    plan = state_raw.get("workflow_plan", {}).get(phase, {})
    active = plan.get("active", []) if isinstance(plan, dict) else []
    history = state_raw.get("step_history") or []
    completed = {
        (e.get("phase"), e.get("step_id"))
        for e in history
        if isinstance(e, dict) and e.get("status") == "completed"
    }
    # ISSUE-16: before marking step as completed, check repeat_until predicate.
    # If predicate returns False, re-emit the same step instead of advancing.
    try:
        contract = load_contract_for_step(just_completed_step_id, state_yaml_path)
    except (FileNotFoundError, ContractError):
        contract = None
    if contract is not None and contract.repeat_until:
        predicate = _REPEAT_PREDICATES.get(contract.repeat_until)
        if predicate is None:
            sys.stderr.write(
                f"[record] unknown repeat_until predicate "
                f"{contract.repeat_until!r} on {just_completed_step_id}; "
                f"treating as absent\n"
            )
        elif not predicate(state_raw):
            return {"phase": phase, "step_id": just_completed_step_id}
    # Include the step we just completed (may not be in history yet if caller
    # invoked record before appending).
    completed.add((phase, just_completed_step_id))
    for sid in active:
        # Handle `{id: foo, ...}` entries plus plain strings and `step if flag`.
        if isinstance(sid, dict):
            sid = sid.get("id", "")
        elif isinstance(sid, str):
            sid = sid.split(" if ")[0].strip()
        if not sid:
            continue
        if (phase, sid) not in completed:
            return {"phase": phase, "step_id": sid}
    return None


def record(state_yaml_path: str, payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Apply a record operation to state.yaml. Returns (result, exit_code).

    Validation: payload.outputs must cover every name in
    contract.expected_outputs (from the contract `outputs:` field).
    """
    required = {"step_id", "phase", "status", "outputs"}
    missing = required - payload.keys()
    if missing:
        return (
            {"action": "error", "reason": "payload_missing_keys", "missing": sorted(missing)},
            3,
        )

    step_id = payload["step_id"]
    phase = payload["phase"]
    status = payload["status"]
    outputs: dict[str, Any] = payload.get("outputs") or {}

    # Load contract to validate expected_outputs
    try:
        contract = load_contract_for_step(step_id, state_yaml_path)
    except FileNotFoundError:
        contract = None

    if contract is not None and status == "completed":
        missing_out = [k for k in contract.outputs if k not in outputs]
        if missing_out:
            return (
                {
                    "action": "validation_error",
                    "reason": "missing_outputs",
                    "step_id": step_id,
                    "missing_outputs": missing_out,
                },
                3,
            )

    # Check A: workflow_plan.active shape on workflow-init completion.
    # Root cause of ISSUE-1: dispatcher reads .active to build the work queue;
    # an empty or missing list causes it to immediately return complete_workflow.
    if step_id == "workflow-init" and status == "completed":
        plan = outputs.get("workflow_plan") or {}
        bad_phases = [
            p for p, body in plan.items()
            if not isinstance(body, dict)
            or not isinstance(body.get("active"), list)
            or len(body["active"]) == 0
        ]
        if bad_phases:
            return (
                {
                    "action": "validation_error",
                    "reason": "workflow_plan_active_missing_or_empty",
                    "phases": bad_phases,
                    "hint": "workflow_plan[<phase>].active must be a non-empty list of step IDs",
                },
                3,
            )

    # Check B: usage required for agent (non-inline) steps on completion.
    # Root cause of ISSUE-10.1: empty usage means cost report is blank and
    # telemetry has no data for the step.
    agent = payload.get("agent", "inline")
    if status == "completed" and agent != "inline":
        usage = payload.get("usage") or {}
        has_tokens = (
            (isinstance(usage.get("input_tokens"), (int, float)) and usage["input_tokens"] > 0)
            or (isinstance(usage.get("output_tokens"), (int, float)) and usage["output_tokens"] > 0)
        )
        if not has_tokens:
            return (
                {
                    "action": "validation_error",
                    "reason": "agent_step_missing_usage",
                    "step_id": step_id,
                    "agent": agent,
                    "hint": "agent steps must record usage.input_tokens or usage.output_tokens > 0",
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
                "action": "error",
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
    history = list(state_raw.get("step_history") or [])
    prior_attempts = [
        e.get("attempt") for e in history
        if isinstance(e, dict) and e.get("phase") == phase and e.get("step_id") == step_id and e.get("attempt")
    ]
    attempt = (max(prior_attempts) + 1) if prior_attempts else 1

    now = _utcnow_iso()

    # ISSUE-17: compute cost_usd live when absent from the payload.
    # Work on a local copy so we never mutate the caller's dict.
    usage: dict[str, Any] = dict(payload.get("usage") or {})
    if agent != "inline" and not usage.get("cost_usd"):
        resolved_model, computed_cost = _compute_cost_usd(agent, usage)
        if resolved_model is not None and computed_cost is not None:
            usage["model"] = resolved_model
            usage["cost_usd"] = computed_cost

    entry: dict[str, Any] = {
        "step_id": step_id,
        "phase": phase,
        "status": status,
        "agent": payload.get("agent", "inline"),
        "attempt": payload.get("attempt", attempt),
        "started_at": payload.get("started_at", now),
        "ended_at": now,
        "usage": usage,
        "evidence": {"outputs": outputs, **(payload.get("evidence") or {})},
    }
    history.append(entry)
    state_raw["step_history"] = history

    # Advance next_step
    next_step = _compute_next_step(state_raw, step_id, state_yaml_path)
    if next_step:
        state_raw["next_step"] = next_step

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
                "action": "error",
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

    return (
        {
            "action": "recorded",
            "step_id": step_id,
            "attempt": entry["attempt"],
            "next_step": next_step,
        },
        0,
    )


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: orchestrator record <state.yaml>  (JSON payload on stdin)",
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
