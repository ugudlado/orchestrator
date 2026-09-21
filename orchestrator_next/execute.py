"""Recording and run-lifecycle helpers shared by the CLI verbs.

The engine spawns nothing: `step` hands every step back to the driver as a
payload, and `done` records what the driver reports. What is left here is the
record-with-retry wrapper, run finalization, and await_input resume routing.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

from orchestrator_next.record import record

def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _log(msg: str) -> None:
    print(f"[{_ts()}] {msg}", file=sys.stderr)


def _now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# Run lifecycle
# ---------------------------------------------------------------------------
def discard_scratch(state_raw: dict) -> bool:
    """Delete the run's scratch dir: artifacts survive, scratch does not.

    Returns True when a directory was removed. Never raises — a run that
    finished should not fail on cleanup of a throwaway directory.
    """
    import shutil

    from orchestrator_next.paths import scratch_dir

    try:
        path = scratch_dir(state_raw)
    except (OSError, ValueError):
        return False
    if not path.is_dir():
        return False
    shutil.rmtree(path, ignore_errors=True)
    return not path.exists()


def _finalize_state(state_yaml_path: str) -> None:
    """Mark a finished run completed, then drop its scratch dir."""
    try:
        with open(state_yaml_path) as f:
            raw = yaml.safe_load(f) or {}
        raw["status"] = "completed"
        raw["next_step"] = None
        path = Path(state_yaml_path)
        path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
    except OSError as exc:
        _log(f"finalize_state: {exc}")
        return
    discard_scratch(raw)


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------
_RECORD_CONFLICT_RETRIES = 6


def _record_with_retry(state_yaml_path: str, payload: dict) -> tuple[dict, int]:
    """record(), retried when another worker won the compare-and-swap.

    Two workers finishing at once both read-modify-write the same run. The
    store refuses the loser (exit 4, `state_write_conflict`) rather than letting
    it clobber — so the loser simply re-reads and re-applies. Its own step
    outcome is unaffected by whatever the winner wrote, because appending a
    history entry and flipping one node's status commute.
    """
    delay = 0.02
    for attempt in range(_RECORD_CONFLICT_RETRIES):
        result, rc = record(state_yaml_path, payload)
        if rc != 4 or (result or {}).get("reason") != "state_write_conflict":
            return result, rc
        time.sleep(delay)
        delay = min(delay * 2, 0.5)
        if attempt == _RECORD_CONFLICT_RETRIES - 2:
            _log(f"WARN: record contention on {payload.get('step_id')} — retrying")
    _log(f"ERROR: could not record {payload.get('step_id')} after "
         f"{_RECORD_CONFLICT_RETRIES} conflict retries")
    return {"reason": "state_write_conflict"}, 4


# ---------------------------------------------------------------------------
# await_input resume routing
#
# A step that records `status: await_input` parks the run and leaves an
# `awaiting: {step_id, ask, options}` block on the state (record.py). These two
# functions turn the user's answer back into a state transition without
# re-dispatching the step. `orchestrator resume` is the verb that calls them;
# `approve` is for gate tokens, which is a different mechanism entirely.
# ---------------------------------------------------------------------------
def match_awaiting_option(text: str, options: list[dict]) -> dict | None:
    """Exact label match, label's first word, or 1-based option number."""
    norm = (text or "").strip().lower()
    if not norm:
        return None
    if norm.isdigit():
        idx = int(norm) - 1
        if 0 <= idx < len(options):
            return options[idx]
        return None
    for opt in options:
        label = str(opt.get("label") or "").strip().lower()
        if not label:
            continue
        if norm == label or norm == label.split()[0]:
            return opt
    return None


def route_awaiting_input(state_yaml_path: str, user_direction: str) -> bool:
    """Apply a matched await_input option to the run; return True when applied.

    Returns False — leaving the state untouched — when the run is not awaiting
    input, offers no options, or the text matches none of them. The caller then
    re-dispatches the step with the raw text for the agent to interpret.
    """
    from orchestrator_next import readiness
    from orchestrator_next.record import _find_workflow_node, _state_from_raw

    try:
        raw = yaml.safe_load(Path(state_yaml_path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return False
    awaiting = raw.get("awaiting")
    if not isinstance(awaiting, dict):
        return False
    options = awaiting.get("options")
    if not isinstance(options, list) or not options:
        return False
    opt = match_awaiting_option(user_direction, options)
    if opt is None:
        return False

    step_id = str(awaiting.get("step_id") or "")
    phase = str(raw.get("phase") or "main")
    label = str(opt.get("label") or "")
    reset_to = str(opt.get("reset_to") or "").strip()
    raw.pop("awaiting", None)

    if reset_to:
        from orchestrator_next.reset_step import apply_dag_reset
        apply_dag_reset(raw, phase, reset_to, keep_history_for=step_id)
        raw["status"] = "active"
        _log(f"awaiting-input: user selected {label!r} → reset to {reset_to!r}")
    else:
        node = _find_workflow_node(raw, phase, step_id)
        if node is not None:
            node["status"] = "completed"
        history = list(raw.get("step_history") or [])
        history.append({
            "step_id": step_id, "phase": phase, "status": "completed",
            "outputs": {"reason": f"user selected: {label}"},
            "attempt": len(
                [h for h in history if isinstance(h, dict) and h.get("step_id") == step_id]
            ) + 1,
        })
        raw["step_history"] = history
        nxt = readiness.next_ready_node(_state_from_raw(raw))
        raw["next_step"] = {"phase": phase, "step_id": nxt} if nxt else None
        _log(f"awaiting-input: user selected {label!r} → advance")

    path = Path(state_yaml_path)
    path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
    discard_scratch(raw)
    return True
