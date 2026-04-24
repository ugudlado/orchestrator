#!/usr/bin/env bash
# remove-worktree.sh — git worktree remove + branch delete.
#
# Env inputs:  REPO_ROOT, WORKTREE_PATH, BRANCH
# Outputs:     {removed: true, worktree_path, branch}
#   or        {removed: false, reason}

set -uo pipefail

REPO_ROOT="${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null)}"
WORKTREE_PATH="${WORKTREE_PATH:-}"
BRANCH="${BRANCH:-}"

if [ -z "$WORKTREE_PATH" ] || [ ! -d "$WORKTREE_PATH" ]; then
  printf '%s\n' "{\"removed\": false, \"reason\": \"worktree path missing: $WORKTREE_PATH\"}"
  exit 0
fi

git -C "$REPO_ROOT" worktree remove "$WORKTREE_PATH" --force 2>/dev/null || {
  printf '%s\n' "{\"removed\": false, \"reason\": \"git worktree remove failed\"}"
  exit 0
}

BRANCH_DELETED=false
BRANCH_WARNING=""
if [ -n "$BRANCH" ]; then
  if git -C "$REPO_ROOT" branch -d "$BRANCH" 2>/dev/null; then
    BRANCH_DELETED=true
  else
    BRANCH_WARNING="Branch $BRANCH not fully merged — skipping deletion."
    printf '%s\n' "$BRANCH_WARNING" >&2
  fi
fi

printf '%s\n' "{\"removed\": true, \"worktree_path\": \"$WORKTREE_PATH\", \"branch\": \"$BRANCH\", \"branch_deleted\": $BRANCH_DELETED, \"branch_warning\": \"$BRANCH_WARNING\"}"
