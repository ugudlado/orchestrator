#!/usr/bin/env bash
# register-with-orchestrator-home.sh — Append repo to metrics registry and ingest archives.
#
# Non-blocking: bootstrap continues even if script exits non-zero.
# Idempotent: register-repo.sh checks before appending.
#
# Env (from dispatch):
#   ORCHESTRATOR_HOME       — path to orchestrator installation (required)
#   ORCHESTRATOR_REPO_ROOT  — absolute path to the project root
#   REPO_ROOT               — fallback
#   METRICS_DB              — optional override for DB path

set -uo pipefail

ORCHESTRATOR_HOME="${ORCHESTRATOR_HOME:-$HOME/.config/orchestrator}"
REPO="${ORCHESTRATOR_REPO_ROOT:-${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null)}}"

REGISTER_SCRIPT="$ORCHESTRATOR_HOME/scripts/register-repo.sh"

if [ ! -f "$REGISTER_SCRIPT" ]; then
  echo "[register-with-orchestrator-home] warn: register-repo.sh not found at $REGISTER_SCRIPT — skipping" >&2
  exit 0
fi

ORCHESTRATOR_HOME="$ORCHESTRATOR_HOME" \
  REPO_ROOT="$REPO" \
  bash "$REGISTER_SCRIPT" || {
    EXIT_CODE=$?
    echo "[register-with-orchestrator-home] warn: register-repo.sh exited $EXIT_CODE — bootstrap continues" >&2
    # Non-blocking: always exit 0
    exit 0
  }

echo "[register-with-orchestrator-home] Metrics registry updated for $REPO"
