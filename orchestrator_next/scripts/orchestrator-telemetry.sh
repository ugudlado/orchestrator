#!/usr/bin/env bash
# orchestrator telemetry — metrics dashboard operator workflow (thin driver)
#
# Usage: orchestrator telemetry
# Step params: config/steps/render-telemetry/contract.yaml (override via env)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_WORKTREE_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [ -d "$_WORKTREE_ROOT/config" ]; then
  export ORCHESTRATOR_HOME="${ORCHESTRATOR_HOME:-$_WORKTREE_ROOT}"
fi

if [ $# -gt 0 ]; then
  echo "ERROR: orchestrator telemetry takes no arguments (set step params via contract.yaml or env)" >&2
  exit 7
fi

PYTHONPATH="${_WORKTREE_ROOT}:${PYTHONPATH:-}" \
  python3 -m orchestrator_next.telemetry_cmd
exit $?
