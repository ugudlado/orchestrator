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


def build_dispatch_env(
    state: Any,
    step_id: str,
    attempt: int,
    state_yaml_path: str = "",
) -> dict[str, str]:
    """ORCHESTRATOR_* block attached to dispatch actions (agent and inline)."""
    change_id = getattr(state, "change_id", "") or ""
    return {
        "ORCHESTRATOR_CHANGE_ID": change_id,
        "ORCHESTRATOR_PHASE": getattr(state, "phase", "") or "main",
        "ORCHESTRATOR_STEP_ID": step_id,
        "ORCHESTRATOR_ATTEMPT": str(attempt),
        "ORCHESTRATOR_WORKFLOW_DIR": getattr(state, "workflow_dir", "") or "",
        "ORCHESTRATOR_REPO_ROOT": getattr(state, "repo_root", "") or "",
        "ORCHESTRATOR_WORKTREE_ARTIFACT_DIR": getattr(state, "worktree_artifact_dir", "") or "",
        **(
            {"ORCHESTRATOR_STATE_YAML_PATH": state_yaml_path}
            if state_yaml_path
            else {}
        ),
    }


def inline_script_env(
    state: Any,
    state_yaml_path: str,
    *,
    action_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Full subprocess env for inline step scripts (bin/orchestrator, rework, etc.)."""
    raw = getattr(state, "raw", None) or {}
    change_id = getattr(state, "change_id", "") or raw.get("slug") or ""
    phase = getattr(state, "phase", "") or "main"
    repo_root = getattr(state, "repo_root", "") or ""
    workflow_dir = getattr(state, "workflow_dir", "") or ""
    worktree_artifact_dir = getattr(state, "worktree_artifact_dir", "") or ""

    merged_action = action_env or {}
    step_id = merged_action.get("ORCHESTRATOR_STEP_ID", "")
    attempt = merged_action.get("ORCHESTRATOR_ATTEMPT", "")

    env: dict[str, str] = {
        **{k: str(v) for k, v in os.environ.items()},
        **{k: str(v) for k, v in merged_action.items()},
        "STATE_YAML_PATH": state_yaml_path,
        "ORCHESTRATOR_STATE_YAML_PATH": state_yaml_path,
        "REPO_ROOT": repo_root,
        "ORCHESTRATOR_REPO_ROOT": repo_root,
        "ORCHESTRATOR_CHANGE_ID": change_id,
        "ORCHESTRATOR_PHASE": phase,
        "ORCHESTRATOR_WORKFLOW_DIR": workflow_dir,
        "ORCHESTRATOR_WORKTREE_ARTIFACT_DIR": worktree_artifact_dir,
    }
    if step_id:
        env["ORCHESTRATOR_STEP_ID"] = str(step_id)
    elif not env.get("ORCHESTRATOR_STEP_ID") and merged_action.get("step_id"):
        env["ORCHESTRATOR_STEP_ID"] = str(merged_action["step_id"])
    if attempt:
        env["ORCHESTRATOR_ATTEMPT"] = str(attempt)

    if change_id:
        env["CHANGE_ID"] = change_id
    branch = raw.get("branch") or ""
    if branch:
        env["BRANCH"] = str(branch)
    worktree = raw.get("worktree_path") or ""
    if worktree:
        worktree = os.path.expanduser(str(worktree))
        env["WORKTREE_PATH"] = worktree
        env["WORKTREE_ROOT"] = worktree
        env["ORCHESTRATOR_WORKFLOW_DIR"] = worktree
    archive_path = raw.get("archive_path") or ""
    if archive_path:
        env["ARCHIVE_PATH"] = str(archive_path)
    repo_root_raw = raw.get("repo_root") or ""
    if repo_root_raw and not env.get("REPO_ROOT"):
        env["REPO_ROOT"] = str(repo_root_raw)
        env["ORCHESTRATOR_REPO_ROOT"] = str(repo_root_raw)

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
