"""Orchestrator-injected environment for workflow step scripts.

All variables below are set by the driver before ``bash config/steps/*/script.sh``
runs. Step scripts must not resolve REPO_ROOT via git, derive ORCHESTRATOR_HOME
from ``BASH_SOURCE``, or read state.yaml for fields the driver already exports.

Canonical names (both legacy and ORCHESTRATOR_* aliases are always set together
when a value exists):

  STATE_YAML_PATH, ORCHESTRATOR_STATE_YAML_PATH
  REPO_ROOT, ORCHESTRATOR_REPO_ROOT
  CHANGE_ID, ORCHESTRATOR_CHANGE_ID
  ORCHESTRATOR_HOME, ORCHESTRATOR_SCRIPTS_DIR
  ORCHESTRATOR_STEP_DIR (set by step_runner; each script.sh resolves its own payload)
  ORCHESTRATOR_PHASE, ORCHESTRATOR_STEP_ID, ORCHESTRATOR_ATTEMPT
  ORCHESTRATOR_WORKFLOW_DIR, ORCHESTRATOR_WORKTREE_ARTIFACT_DIR
  WORKTREE_PATH, WORKTREE_ROOT (when worktree_path in state)
  ARCHIVE_PATH, BRANCH (when present in state)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _orchestrator_home_default() -> str:
    root = Path(__file__).resolve().parent.parent
    if (root / "config").is_dir():
        return str(root)
    return os.environ.get("ORCHESTRATOR_HOME", "")


def _apply_home_paths(env: dict[str, str]) -> None:
    home = env.get("ORCHESTRATOR_HOME", "")
    if not home:
        default = _orchestrator_home_default()
        if default:
            home = default
            env["ORCHESTRATOR_HOME"] = home
    if home:
        env["ORCHESTRATOR_SCRIPTS_DIR"] = str(Path(home) / "orchestrator_next" / "scripts")


def _resolve_prior_outputs(state: Any, step_id: str) -> dict[str, Any]:
    """Collect declared inputs for step_id from completed step_history entries.

    For each name in node["inputs"], find the most-recent completed step_history
    entry whose evidence.outputs contains that key. Returns a flat dict of
    {input_name: value} — only keys declared in inputs are included.
    """
    from orchestrator_next.parser import phase_nodes
    from orchestrator_next.readiness import find_node

    nodes = phase_nodes(state, state.phase)
    node = find_node(nodes, step_id)
    if node is None:
        return {}
    declared_inputs: list[str] = [str(k) for k in (node.get("inputs") or []) if k]
    if not declared_inputs:
        return {}

    history = getattr(state, "step_history", []) or []
    # Walk history most-recent-first to get the latest value for each input key.
    result: dict[str, Any] = {}
    remaining = set(declared_inputs)
    for entry in reversed(history):
        if not remaining:
            break
        if getattr(entry, "status", None) not in ("completed", "recovered"):
            continue
        raw = getattr(entry, "raw", {}) or {}
        evidence = raw.get("evidence") or {}
        outputs = evidence.get("outputs") or {}
        for key in list(remaining):
            if key in outputs:
                result[key] = outputs[key]
                remaining.discard(key)
    return result


def build_dispatch_env(
    state: Any,
    step_id: str,
    attempt: int,
    state_yaml_path: str = "",
) -> dict[str, str]:
    """ORCHESTRATOR_* block attached to dispatch actions (agent and inline)."""
    change_id = getattr(state, "change_id", "") or ""
    prior_outputs = _resolve_prior_outputs(state, step_id)
    env: dict[str, str] = {
        "ORCHESTRATOR_CHANGE_ID": change_id,
        "ORCHESTRATOR_PHASE": getattr(state, "phase", "") or "main",
        "ORCHESTRATOR_STEP_ID": step_id,
        "ORCHESTRATOR_ATTEMPT": str(attempt),
        "ORCHESTRATOR_WORKFLOW_DIR": getattr(state, "workflow_dir", "") or "",
        "ORCHESTRATOR_REPO_ROOT": getattr(state, "repo_root", "") or "",
        "ORCHESTRATOR_WORKTREE_ARTIFACT_DIR": getattr(state, "worktree_artifact_dir", "") or "",
    }
    if state_yaml_path:
        env["ORCHESTRATOR_STATE_YAML_PATH"] = state_yaml_path
    if prior_outputs:
        env["ORCHESTRATOR_PRIOR_OUTPUTS"] = json.dumps(prior_outputs)
    return env


def inline_script_env(
    state: Any,
    state_yaml_path: str,
    *,
    action_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Full subprocess env for inline step scripts (bin/orchestrator, rework, etc.)."""
    raw = getattr(state, "raw", None) or {}
    change_id = getattr(state, "change_id", "") or raw.get("slug") or ""
    repo_root = getattr(state, "repo_root", "") or ""

    # Start with os.environ, then overlay action_env (which already carries the
    # canonical ORCHESTRATOR_* fields from build_dispatch_env).
    env: dict[str, str] = {
        **{k: str(v) for k, v in os.environ.items()},
        **(action_env or {}),
    }

    # Script-specific fields not present in the dispatch env.
    env["STATE_YAML_PATH"] = state_yaml_path
    env["ORCHESTRATOR_STATE_YAML_PATH"] = state_yaml_path
    if repo_root:
        env.setdefault("REPO_ROOT", repo_root)
        env.setdefault("ORCHESTRATOR_REPO_ROOT", repo_root)
    if change_id:
        env["CHANGE_ID"] = change_id
        env.setdefault("ORCHESTRATOR_CHANGE_ID", change_id)
    branch = raw.get("branch") or ""
    if branch:
        env["BRANCH"] = str(branch)
    # worktree_path is already expanded in state.workflow_dir (and thus
    # ORCHESTRATOR_WORKFLOW_DIR in action_env), but legacy scripts also read
    # WORKTREE_PATH / WORKTREE_ROOT directly.
    worktree = raw.get("worktree_path") or ""
    if worktree:
        worktree = os.path.expanduser(str(worktree))
        env["WORKTREE_PATH"] = worktree
        env["WORKTREE_ROOT"] = worktree
        env["ORCHESTRATOR_WORKFLOW_DIR"] = worktree
    elif not env.get("ORCHESTRATOR_WORKFLOW_DIR"):
        workflow_dir = getattr(state, "workflow_dir", "") or ""
        if workflow_dir:
            env["ORCHESTRATOR_WORKFLOW_DIR"] = workflow_dir
    archive_path = raw.get("archive_path") or ""
    if archive_path:
        env["ARCHIVE_PATH"] = str(archive_path)

    _apply_home_paths(env)
    return env


def operator_script_env(
    repo_root: str,
    *,
    state_yaml_path: str = "/dev/null",
    step_id: str = "",
) -> dict[str, str]:
    """Env for operator workflows (telemetry, learn) without a feature state.yaml."""
    env: dict[str, str] = {
        **{k: str(v) for k, v in os.environ.items()},
        "REPO_ROOT": repo_root,
        "ORCHESTRATOR_REPO_ROOT": repo_root,
        "STATE_YAML_PATH": state_yaml_path,
        "ORCHESTRATOR_STATE_YAML_PATH": state_yaml_path,
        "ORCHESTRATOR_WORKFLOW_DIR": repo_root,
    }
    if step_id:
        env["ORCHESTRATOR_STEP_ID"] = step_id
    _apply_home_paths(env)
    return env
