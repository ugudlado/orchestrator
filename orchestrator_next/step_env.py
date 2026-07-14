"""Orchestrator-injected environment for workflow step scripts.

All variables below are set by the driver before ``bash config/steps/*/script.sh``
runs. Step scripts must not resolve REPO_ROOT via git, derive ORCHESTRATOR_HOME
from ``BASH_SOURCE``, or read state.yaml for fields the driver already exports.

Canonical names (both legacy and ORCHESTRATOR_* aliases are always set together
when a value exists):

  STATE_YAML_PATH, ORCHESTRATOR_STATE_YAML_PATH
  REPO_ROOT, ORCHESTRATOR_REPO_ROOT
  CHANGE_ID, ORCHESTRATOR_CHANGE_ID
  ORCHESTRATOR_HOME
  ORCHESTRATOR_STEP_DIR (set by run_loop; each script.sh resolves its own payload)
  ORCHESTRATOR_PHASE, ORCHESTRATOR_STEP_ID, ORCHESTRATOR_ATTEMPT
  ORCHESTRATOR_WORKFLOW_DIR, ORCHESTRATOR_WORKTREE_ARTIFACT_DIR
  WORKTREE_PATH, WORKTREE_ROOT (when worktree_path in state)
  ARCHIVE_PATH, BRANCH (when present in state)
"""

from __future__ import annotations

import os
from typing import Any


def _apply_home_paths(env: dict[str, str]) -> None:
    home = env.get("ORCHESTRATOR_HOME", "")
    if not home:
        # Steps reference $ORCHESTRATOR_HOME/config/... — derive home from the
        # explicit config root. ponytail: assumes the config dir is literally
        # named config/ (true for the repo and the bundled package layout);
        # rename step prompts to $ORCHESTRATOR_CONFIG if that ever breaks.
        from orchestrator_next.paths import ConfigRootError, config_root
        try:
            home = str(config_root().parent)
            env["ORCHESTRATOR_HOME"] = home
        except ConfigRootError:
            pass


def build_dispatch_env(
    state: Any,
    step_id: str,
    attempt: int,
    state_yaml_path: str = "",
) -> dict[str, str]:
    """ORCHESTRATOR_* block attached to dispatch actions (agent and inline)."""
    change_id = getattr(state, "change_id", "") or ""
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
