#!/usr/bin/env bash
# check-linear-config.sh — Check if repo is registered in Linear config.
# Informational only — never blocks bootstrap.
#
# Idempotent: read-only operation, safe to re-run.
#
# Env (from dispatch):
#   ORCHESTRATOR_REPO_ROOT  — absolute path to the project root
#   REPO_ROOT               — fallback

set -euo pipefail

REPO="${ORCHESTRATOR_REPO_ROOT:-${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null)}}"
LINEAR_CONFIG="$HOME/.config/linear/config.yaml"

REPO_NAME=$(basename "$REPO")

if [ ! -f "$LINEAR_CONFIG" ]; then
  echo "[check-linear-config] Linear config not found at $LINEAR_CONFIG"
  echo "[check-linear-config] Linear: not configured for \"$REPO_NAME\""
  echo "[check-linear-config]   To enable: create ~/.config/linear/config.yaml"
  echo "[check-linear-config]   Workflow will use --no-linear for this repo."
  exit 0
fi

if grep -q "\"$REPO_NAME\"\|'$REPO_NAME'\|$REPO_NAME:" "$LINEAR_CONFIG" 2>/dev/null; then
  echo "[check-linear-config] Linear: enabled for \"$REPO_NAME\" with configured labels"
else
  echo "[check-linear-config] Linear: not configured for \"$REPO_NAME\""
  echo "[check-linear-config]   To enable: add a repo entry in ~/.config/linear/config.yaml"
  echo "[check-linear-config]   Workflow will use --no-linear for this repo."
fi
