#!/usr/bin/env bash
# commit-worktree-learn-updates.sh — stage+commit learn-cycle edits in the feature worktree.
#
# Purpose: keep learn-cycle/rule edits off `main` by committing them on the
# feature branch before merge-to-main.
#
# Usage:
#   commit-worktree-learn-updates.sh <worktree_dir> <ticket_slug> [branch]
#
# Behavior:
# - Stages only safe global config/rule paths (avoids spec/changes artifacts).
# - If staged changes exist, commits with message "chore(<ticket_slug>): learn-cycle rule updates".
# - If any uncommitted changes remain after that, exits 7 (caller should stop before merge).
set -euo pipefail

WT_DIR="${1:-}"
TICKET_SLUG="${2:-}"
BRANCH="${3:-}"

if [ -z "$WT_DIR" ] || [ -z "$TICKET_SLUG" ]; then
  echo "Usage: commit-worktree-learn-updates.sh <worktree_dir> <ticket_slug> [branch]" >&2
  exit 1
fi

if [ ! -d "$WT_DIR" ] || ! git -C "$WT_DIR" rev-parse --git-dir >/dev/null 2>&1; then
  # No worktree repo available; nothing to do.
  exit 0
fi

if [ -n "$BRANCH" ]; then
  WT_HEAD="$(git -C "$WT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")"
  if [ "$WT_HEAD" != "$BRANCH" ]; then
    git -C "$WT_DIR" checkout "$BRANCH" >/dev/null 2>&1 || true
  fi
fi

if [ -z "$(git -C "$WT_DIR" status --porcelain)" ]; then
  exit 0
fi

# Stage only safe global config/rule paths (avoid spec/changes artifacts).
# IMPORTANT: `git add` fails the whole invocation if any pathspec is missing,
# so we add paths individually when present.
for p in config scripts skills .orchestrator spec/project.yaml; do
  if [ -e "$WT_DIR/$p" ]; then
    git -C "$WT_DIR" add -A "$p" 2>/dev/null || true
  fi
done

if ! git -C "$WT_DIR" diff --cached --quiet; then
  COMMIT_MSG="chore(${TICKET_SLUG}): learn-cycle rule updates"
  git -C "$WT_DIR" commit -m "$COMMIT_MSG" >/dev/null 2>&1 || true
fi

if [ -n "$(git -C "$WT_DIR" status --porcelain)" ]; then
  echo "ERROR: worktree has uncommitted changes after learn-cycle auto-commit; refusing to merge." >&2
  echo "       worktree=$WT_DIR ticket=$TICKET_SLUG" >&2
  git -C "$WT_DIR" status --porcelain >&2 || true
  exit 7
fi

exit 0

