#!/usr/bin/env bash
# remove-worktree.sh — git worktree remove (branch is always retained).
#
# Env inputs:  REPO_ROOT, WORKTREE_PATH, BRANCH
# Outputs:     {removed: true, worktree_path, branch}
#   or        {removed: false, reason}

set -uo pipefail

REPO_ROOT="${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null)}"
WORKTREE_PATH="${WORKTREE_PATH:-}"
BRANCH="${BRANCH:-}"

if [[ -n "${STATE_YAML_PATH:-}" && -f "$STATE_YAML_PATH" ]]; then
  # shellcheck source=./_read_state_env.sh
  source "$(dirname "$0")/_read_state_env.sh"
  read_state_env "$STATE_YAML_PATH" WORKTREE_PATH BRANCH REPO_ROOT
  WORKTREE_PATH="${WORKTREE_PATH/#\~/$HOME}"
  REPO_ROOT="${REPO_ROOT:-}"
fi

if [ -z "$WORKTREE_PATH" ] || [ ! -d "$WORKTREE_PATH" ]; then
  printf '%s\n' "{\"removed\": false, \"reason\": \"worktree path missing: $WORKTREE_PATH\"}"
  exit 0
fi

git -C "$REPO_ROOT" worktree remove "$WORKTREE_PATH" --force 2>/dev/null || {
  printf '%s\n' "{\"removed\": false, \"reason\": \"git worktree remove failed\"}"
  exit 0
}

printf '%s\n' "{\"removed\": true, \"worktree_path\": \"$WORKTREE_PATH\", \"branch\": \"$BRANCH\", \"branch_deleted\": false}"
