#!/usr/bin/env bash
# remove-worktree — remove feature worktree after merge.
set -euo pipefail

: "${REPO_ROOT:?orchestrator: REPO_ROOT required}"

STATE_YAML="${ORCHESTRATOR_STATE_YAML_PATH:-${STATE_YAML_PATH:?orchestrator: state yaml path required}}"

_read_state_field() {
  python3 -c "
import sys, yaml
raw = yaml.safe_load(open('$STATE_YAML')) or {}
print(raw.get('$1') or '')
" 2>/dev/null || true
}

WORKTREE_PATH="${WORKTREE_PATH:-$(_read_state_field worktree_path)}"
BRANCH="${BRANCH:-$(_read_state_field branch)}"

TEARDOWN_SCRIPT="$(dirname "$0")/../../../../orchestrator_next/scripts/complete/remove-worktree.sh"

REPO_ROOT="$REPO_ROOT" WORKTREE_PATH="$WORKTREE_PATH" BRANCH="$BRANCH" bash "$TEARDOWN_SCRIPT"
