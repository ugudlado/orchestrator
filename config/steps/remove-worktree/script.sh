#!/usr/bin/env bash
# remove-worktree — remove feature worktree after merge.
#
# Env (injected by orchestrator): REPO_ROOT, STATE_YAML_PATH
# Outputs: {removed: true, worktree_path, branch} or {removed: false, reason}

set -uo pipefail

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

WORKTREE_PATH="${WORKTREE_PATH/#\~/$HOME}"

if [ -z "$WORKTREE_PATH" ] || [ ! -d "$WORKTREE_PATH" ]; then
  printf '%s\n' "{\"removed\": false, \"reason\": \"worktree path missing: $WORKTREE_PATH\"}"
  exit 0
fi

git -C "$REPO_ROOT" worktree remove "$WORKTREE_PATH" --force 2>/dev/null || {
  printf '%s\n' "{\"removed\": false, \"reason\": \"git worktree remove failed\"}"
  exit 0
}

printf '%s\n' "{\"removed\": true, \"worktree_path\": \"$WORKTREE_PATH\", \"branch\": \"$BRANCH\", \"branch_deleted\": false}"
