#!/usr/bin/env bash
# git-init.sh — Ensure the repo is a git repository. Initialize if not.
#
# Idempotent: if .git already exists, skips silently.
# Never creates an initial commit.
#
# Env (from dispatch):
#   ORCHESTRATOR_REPO_ROOT  — absolute path to the project root
#   REPO_ROOT               — fallback

set -euo pipefail

REPO="${ORCHESTRATOR_REPO_ROOT:-${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null)}}"

if [ -z "$REPO" ]; then
  echo "[git-init] error: ORCHESTRATOR_REPO_ROOT is not set and git rev-parse failed" >&2
  exit 1
fi

if [ -d "$REPO/.git" ]; then
  echo "[git-init] git already initialized — skipping"
  exit 0
fi

git init "$REPO"
echo "[git-init] git init complete"
