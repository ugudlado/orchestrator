#!/usr/bin/env bash
# workflow-init.sh — bootstrap a new workflow: worktree + artifact dir + project context.
#
# Env inputs:  REPO_ROOT, CHANGE_ID, SLUG, SCHEMA
#              WORKFLOW_STATE_DIR — parent of per-feature state dirs
#              ORCHESTRATOR_HOME  — path to orchestrator config
#              FLAGS_WORKTREE     — "true" | "false" (default: true)
#              FLAGS_LINEAR       — "true" | "false" (skipped; linear_ticket_id always null)
# Outputs (last stdout line, JSON):
#   {worktree_path, branch, linear_ticket_id, workflow_plan, resolved_flags, plan_yaml_path}

set -uo pipefail

REPO_ROOT="${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null)}"
CHANGE_ID="${CHANGE_ID:-${SLUG:-${ORCHESTRATOR_CHANGE_ID:-}}}"
WORKFLOW_STATE_DIR="${WORKFLOW_STATE_DIR:-$REPO_ROOT/spec/changes}"
ORCHESTRATOR_HOME="${ORCHESTRATOR_HOME:-$HOME/.config/orchestrator}"
FLAGS_WORKTREE="${FLAGS_WORKTREE:-true}"
WORKTREE_BASE_DIR="${WORKTREE_BASE_DIR:-$HOME/code/feature_worktrees}"

if [ -z "$CHANGE_ID" ]; then
  printf '%s\n' '{"error": "CHANGE_ID (or SLUG) is required"}' >&2
  exit 1
fi

STATE_YAML="$WORKFLOW_STATE_DIR/$CHANGE_ID/state.yaml"

if [ ! -f "$STATE_YAML" ]; then
  printf 'error: state.yaml not found at %s\n' "$STATE_YAML" >&2
  exit 1
fi

BRANCH="orc/$CHANGE_ID"
WORKTREE_PATH=""

# --- Worktree creation -------------------------------------------------------
if [ "$FLAGS_WORKTREE" = "true" ]; then
  WORKTREE_PATH="$WORKTREE_BASE_DIR/$CHANGE_ID"
  mkdir -p "$WORKTREE_BASE_DIR"

  if git -C "$REPO_ROOT" worktree list --porcelain | grep -q "worktree $WORKTREE_PATH"; then
    printf 'worktree already exists at %s\n' "$WORKTREE_PATH" >&2
  else
    git -C "$REPO_ROOT" worktree add -b "$BRANCH" "$WORKTREE_PATH" HEAD 2>&1 >&2 || {
      # Branch may already exist if this is a resume — try without -b
      git -C "$REPO_ROOT" worktree add "$WORKTREE_PATH" "$BRANCH" 2>&1 >&2 || {
        printf 'error: git worktree add failed\n' >&2
        exit 1
      }
    }
  fi

  # Ensure artifact directory exists in worktree (required before writer steps)
  mkdir -p "$WORKTREE_PATH/spec/changes/$CHANGE_ID"
fi

# --- Mark project context loaded in state.yaml -------------------------------
python3 - "$STATE_YAML" "$WORKTREE_PATH" "$BRANCH" <<'PYEOF'
import sys
import yaml
from pathlib import Path

state_yaml = sys.argv[1]
worktree_path = sys.argv[2] or None
branch = sys.argv[3] or None

p = Path(state_yaml)
state = yaml.safe_load(p.read_text())

state["project_context_loaded"] = True
if worktree_path:
    state["worktree_path"] = worktree_path
if branch:
    state["branch"] = branch

p.write_text(yaml.safe_dump(state, sort_keys=False, allow_unicode=True))
print("state.yaml updated", file=sys.stderr)
PYEOF

PYTHON_EXIT=$?
if [ $PYTHON_EXIT -ne 0 ]; then
  printf 'error: failed to update state.yaml\n' >&2
  exit 1
fi

# --- Emit JSON outputs -------------------------------------------------------
# workflow_plan and resolved_flags are already in state.yaml from seed-state.
# plan.yaml is already written by seed-state. We just surface the paths.
PLAN_YAML="$WORKFLOW_STATE_DIR/$CHANGE_ID/plan.yaml"

python3 - "$STATE_YAML" "$PLAN_YAML" "$WORKTREE_PATH" "$BRANCH" <<'PYEOF'
import sys
import json
import yaml
from pathlib import Path

state_yaml = sys.argv[1]
plan_yaml  = sys.argv[2]
worktree_path = sys.argv[3] or None
branch = sys.argv[4] or None

state = yaml.safe_load(Path(state_yaml).read_text())

out = {
    "worktree_path": worktree_path,
    "branch": branch,
    "linear_ticket_id": None,
    "workflow_plan": state.get("workflow_plan"),
    "resolved_flags": state.get("flags"),
    "plan_yaml_path": plan_yaml if Path(plan_yaml).exists() else None,
}
print(json.dumps(out))
PYEOF
