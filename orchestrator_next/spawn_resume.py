"""
Clear spawn_failure_cap state when the shell workflow loop is (re)started.

`orchestrator run` / run-workflow.sh call `apply_spawn_failure_resume` once before
the dispatch loop. That lets an operator fix the underlying tool issue (e.g. Claude
usage) and rerun without hand-editing step_history. The cap still applies inside a
single loop if spawns keep failing.

Does not clear architect escalation, agent-blocked, or abandoned steps.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

from orchestrator_next import readiness
from orchestrator_next.dispatch import (
    _consecutive_spawn_failures,
    _is_spawn_failure,
    _max_spawn_failures,
)
from orchestrator_next.parser import _parse_history_entry


def _resume_step_id(state) -> str | None:
    next_step = state.raw.get("next_step")
    if isinstance(next_step, dict):
        step_id = next_step.get("step_id")
        if isinstance(step_id, str) and step_id:
            return step_id
    return readiness.next_ready_node(state)


def clear_spawn_failure_cap_in_raw(state_raw: dict[str, Any]) -> bool:
    """
    Drop trailing zero-token spawn failures for the next step and un-block state.

    Returns True when state_raw was modified.
    """
    phase = state_raw.get("phase") or "main"
    state = load_state_from_raw(state_raw)
    step_id = _resume_step_id(state)
    if not step_id:
        return False

    history: list[dict[str, Any]] = list(state_raw.get("step_history") or [])
    if not history:
        return False

    spawn_count = _consecutive_spawn_failures(state.step_history, phase, step_id)
    if spawn_count < _max_spawn_failures(state_raw):
        return False

    # Most recent row for this step must be a spawn failure (not architect/blocked).
    last_for_step: dict[str, Any] | None = None
    for entry in reversed(history):
        if not isinstance(entry, dict):
            break
        if entry.get("phase", "main") != phase or entry.get("step_id") != step_id:
            continue
        last_for_step = entry
        break
    if last_for_step is None:
        return False
    if not _is_spawn_failure(_parse_history_entry(last_for_step)):
        return False

    removed = 0
    while history:
        last = history[-1]
        if not isinstance(last, dict):
            break
        if last.get("phase", "main") != phase or last.get("step_id") != step_id:
            break
        if not _is_spawn_failure(_parse_history_entry(last)):
            break
        history.pop()
        removed += 1

    if removed == 0:
        return False

    state_raw["step_history"] = history
    if state_raw.get("status") in ("blocked", "paused"):
        state_raw["status"] = "in_progress"

    print(
        f"Resuming after spawn_failure_cap: cleared {removed} zero-token "
        f"failure(s) for {phase}/{step_id}; status=in_progress",
        file=sys.stderr,
    )
    return True


def load_state_from_raw(state_raw: dict[str, Any]):
    """Build a State view without reading from disk (for tests)."""
    from orchestrator_next.parser import State

    history = [
        _parse_history_entry(e)
        for e in (state_raw.get("step_history") or [])
        if isinstance(e, dict)
    ]
    return State(
        change_id=state_raw.get("change_id") or state_raw.get("slug") or "",
        phase=state_raw.get("phase") or "main",
        repo_root=state_raw.get("repo_root") or "",
        workflow_dir=state_raw.get("worktree_path") or state_raw.get("repo_root") or "",
        workflow_plan=state_raw.get("workflow_plan") or {},
        step_history=history,
        raw=state_raw,
    )


def apply_spawn_failure_resume(state_yaml_path: str) -> bool:
    path = Path(state_yaml_path)
    pre = path.read_bytes()
    with open(path, encoding="utf-8") as f:
        state_raw = yaml.safe_load(f) or {}
    if not isinstance(state_raw, dict):
        raise ValueError(f"state.yaml root must be a mapping: {path}")

    if not clear_spawn_failure_cap_in_raw(state_raw):
        return False

    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(state_raw, f, sort_keys=False, default_flow_style=False)
    try:
        with open(path, encoding="utf-8") as f:
            yaml.safe_load(f)
    except yaml.YAMLError:
        path.write_bytes(pre)
        raise
    return True


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("Usage: python -m orchestrator_next.spawn_resume <state.yaml>", file=sys.stderr)
        return 2
    apply_spawn_failure_resume(args[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
