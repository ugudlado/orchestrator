#!/usr/bin/env bash
# create-worktree — create feature worktree for implementation artifacts.
#
# Always creates a worktree. Re-uses worktree_path if already set in state.
#
# Remote (cloud/Slack) sessions already run in an isolated sandbox on their own
# branch — a local worktree would just misdirect every downstream step into a
# detached dir instead of the branch the PR flow surfaces. No-op there, same
# convention as the ticket-* steps' cloud no-op (see DRIVE.md).
#
# Env (injected by orchestrator): REPO_ROOT, CHANGE_ID, STATE_YAML_PATH
# Outputs: {created, worktree_path, branch} or {created: false, reason}
# state_patch: worktree_path + branch written back to state when created.

set -uo pipefail

: "${REPO_ROOT:?orchestrator: REPO_ROOT required}"
: "${CHANGE_ID:?orchestrator: CHANGE_ID required}"

if [ "${CLAUDE_CODE_REMOTE:-}" = "true" ]; then
  printf '%s\n' '{"created": false, "reason": "remote session — using sandbox checkout in place, no local worktree"}'
  exit 0
fi

STATE_YAML="${ORCHESTRATOR_STATE_YAML_PATH:-${STATE_YAML_PATH:?orchestrator: STATE_YAML_PATH required}}"
WORKTREE_BASE_DIR="${WORKTREE_BASE_DIR:-$HOME/code/feature_worktrees}"

# Read existing worktree_path and schema from state.
read_state() {
  python3 -c "
import sys, yaml
raw = yaml.safe_load(open('$STATE_YAML')) or {}
print(raw.get('worktree_path') or '')
print(raw.get('schema') or '')
" 2>/dev/null || printf '\n\n'
}

IFS=$'\n' read -r _EXISTING_WT _SCHEMA <<< "$(read_state)"

SCHEMA="${_SCHEMA:-feature}"
BRANCH="$SCHEMA/$CHANGE_ID"
WORKTREE_PATH="$WORKTREE_BASE_DIR/$CHANGE_ID"

# Re-use existing worktree if already present.
if [ -n "$_EXISTING_WT" ] && [ -d "$_EXISTING_WT" ]; then
  printf '%s\n' "{\"created\": false, \"reason\": \"worktree already exists\", \"worktree_path\": \"$_EXISTING_WT\", \"branch\": \"$BRANCH\"}"
  exit 0
fi

mkdir -p "$WORKTREE_BASE_DIR"

if git -C "$REPO_ROOT" worktree list --porcelain | grep -q "worktree $WORKTREE_PATH"; then
  printf '%s\n' "{\"created\": false, \"reason\": \"worktree already registered\", \"worktree_path\": \"$WORKTREE_PATH\", \"branch\": \"$BRANCH\", \"state_patch\": {\"worktree_path\": \"$WORKTREE_PATH\", \"branch\": \"$BRANCH\"}}"
  exit 0
fi

git -C "$REPO_ROOT" worktree add -b "$BRANCH" "$WORKTREE_PATH" HEAD 2>&1 >&2 || {
  git -C "$REPO_ROOT" worktree add "$WORKTREE_PATH" "$BRANCH" 2>&1 >&2 || {
    printf '%s\n' '{"created": false, "reason": "git worktree add failed"}'
    exit 1
  }
}

printf '%s\n' "{\"created\": true, \"worktree_path\": \"$WORKTREE_PATH\", \"branch\": \"$BRANCH\", \"state_patch\": {\"worktree_path\": \"$WORKTREE_PATH\", \"branch\": \"$BRANCH\"}}"
