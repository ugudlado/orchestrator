#!/usr/bin/env bash
# commit-worktree-learn-updates.sh — stage+commit learn-cycle edits on the
# current branch of a repo dir (feature worktree OR main checkout).
#
# Purpose: keep the working tree clean after a learn run, wherever it ran.
#   - Orchestrated worktree run: commits rule edits on the feature branch
#     before merge-to-main (so they don't land on main as a dirty tree).
#   - Standalone /learn (workflow-learner agent, no worktree): commits the
#     same edits on whatever branch is checked out, so main never accumulates
#     an uncommitted learn diff. ORC: this is the chokepoint both entry points
#     share, so the agent calls this as its last act.
#
# Usage:
#   commit-worktree-learn-updates.sh <repo_dir> <ticket_slug> [branch] [--require-clean]
#
# Behavior:
# - Stages ONLY learn's write targets (config/steps, .orchestrator,
#   spec/project.yaml) — never whole dirs, so unrelated WIP (e.g. engine
#   code under config/scripts) is left untouched.
# - If staged changes exist, commits "chore(<ticket_slug>): learn-cycle rule updates".
# - --require-clean: after committing, if ANY uncommitted changes remain, exit 7.
#   Use this only on the pre-merge worktree path (a dirty tree must not be merged).
#   Without it (standalone path), unrelated WIP is fine — commit-and-return.
set -euo pipefail

REPO_DIR="${1:-}"
TICKET_SLUG="${2:-}"
BRANCH="${3:-}"
REQUIRE_CLEAN=false
if [ "${4:-}" = "--require-clean" ]; then
  REQUIRE_CLEAN=true
fi

if [ -z "$REPO_DIR" ] || [ -z "$TICKET_SLUG" ]; then
  echo "Usage: commit-worktree-learn-updates.sh <repo_dir> <ticket_slug> [branch] [--require-clean]" >&2
  exit 1
fi

if [ ! -d "$REPO_DIR" ] || ! git -C "$REPO_DIR" rev-parse --git-dir >/dev/null 2>&1; then
  # No repo available; nothing to do.
  exit 0
fi

if [ -n "$BRANCH" ]; then
  CUR_HEAD="$(git -C "$REPO_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")"
  if [ "$CUR_HEAD" != "$BRANCH" ]; then
    git -C "$REPO_DIR" checkout "$BRANCH" >/dev/null 2>&1 || true
  fi
fi

if [ -z "$(git -C "$REPO_DIR" status --porcelain)" ]; then
  exit 0
fi

# Stage ONLY learn's write targets. learn (workflow-learner agent) routes to:
#   - config/steps/**         (global step contracts)
#   - .orchestrator/**        (repo-specific contract overrides)
#   - spec/project.yaml       (project learnings / quality bar)
# Staging whole dirs would sweep in unrelated WIP, so we pathspec narrowly.
# `git add` fails the whole invocation if a pathspec matches nothing, so add
# each present path individually.
for p in config/steps .orchestrator spec/project.yaml; do
  if [ -e "$REPO_DIR/$p" ]; then
    git -C "$REPO_DIR" add -A "$p" 2>/dev/null || true
  fi
done

if ! git -C "$REPO_DIR" diff --cached --quiet; then
  COMMIT_MSG="chore(${TICKET_SLUG}): learn-cycle rule updates"
  git -C "$REPO_DIR" commit -m "$COMMIT_MSG" >/dev/null 2>&1 || true
fi

# Merge-path guard only: a dirty tree must not be merged. The standalone path
# tolerates unrelated WIP, so it skips this check.
if [ "$REQUIRE_CLEAN" = true ] && [ -n "$(git -C "$REPO_DIR" status --porcelain)" ]; then
  echo "ERROR: worktree has uncommitted changes after learn-cycle auto-commit; refusing to merge." >&2
  echo "       repo=$REPO_DIR ticket=$TICKET_SLUG" >&2
  git -C "$REPO_DIR" status --porcelain >&2 || true
  exit 7
fi

exit 0
