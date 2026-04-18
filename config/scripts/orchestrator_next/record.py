"""HL-287 M5: orchestrator record subcommand.

Accepts a completed step's outputs + usage + evidence via stdin JSON,
validates against the contract's `expected_outputs`, writes a terminal
`step_history` entry with uniform `started_at` / `completed_at` / `usage`,
and advances `next_step` per `workflow_plan`.
"""
from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from orchestrator_next.parser import load_contract_for_step, load_state


def _utcnow_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _compute_next_step(state_raw: dict[str, Any], just_completed_step_id: str) -> dict[str, Any] | None:
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

    path = Path(state_yaml_path)
    with open(path) as f:
        state_raw = yaml.safe_load(f) or {}

    # Determine attempt number for this (phase, step_id).
    history = list(state_raw.get("step_history") or [])
    prior_attempts = [
        e.get("attempt") for e in history
        if isinstance(e, dict) and e.get("phase") == phase and e.get("step_id") == step_id and e.get("attempt")
    ]
    attempt = (max(prior_attempts) + 1) if prior_attempts else 1

    now = _utcnow_iso()
    entry: dict[str, Any] = {
        "step_id": step_id,
        "phase": phase,
        "status": status,
        "agent": payload.get("agent", "inline"),
        "attempt": payload.get("attempt", attempt),
        "started_at": payload.get("started_at", now),
        "ended_at": now,
        "usage": payload.get("usage") or {},
        "evidence": {"outputs": outputs, **(payload.get("evidence") or {})},
    }
    history.append(entry)
    state_raw["step_history"] = history

    # Advance next_step
    next_step = _compute_next_step(state_raw, step_id)
    if next_step:
        state_raw["next_step"] = next_step

    with open(path, "w") as f:
        yaml.safe_dump(state_raw, f, sort_keys=False, default_flow_style=False)

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
