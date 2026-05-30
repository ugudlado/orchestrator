#!/usr/bin/env bash
# orchestrator learn / workflow-learner — metrics prep (thin driver)
#
# Usage: orchestrator learn <change-id-or-state-yaml>
# Step params: config/steps/gather-learn-metrics/contract.yaml (override via env)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_WORKTREE_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
METRICS_DIR="$SCRIPT_DIR/metrics"

CHANGE_ID=""

usage() {
  echo "Usage: orchestrator learn <change-id-or-state-yaml>" >&2
  exit 7
}

while [ $# -gt 0 ]; do
  case "$1" in
    --help|-h)
      usage
      ;;
    -*)
      echo "ERROR: unknown option: $1 (set LEARN_SCOPE etc. via env or step contract)" >&2
      usage
      ;;
    *)
      if [ -z "$CHANGE_ID" ]; then
        CHANGE_ID="$1"
        shift
      else
        echo "ERROR: unexpected argument: $1" >&2
        usage
      fi
      ;;
  esac
done

[ -n "$CHANGE_ID" ] || usage

if [ -d "$_WORKTREE_ROOT/config" ]; then
  export ORCHESTRATOR_HOME="${ORCHESTRATOR_HOME:-$_WORKTREE_ROOT}"
fi

REPO_ROOT="${REPO_ROOT:-$(git -C "$_WORKTREE_ROOT" rev-parse --show-toplevel 2>/dev/null || true)}"
if [ -z "$REPO_ROOT" ] || [ ! -f "$REPO_ROOT/spec/project.yaml" ]; then
  REPO_ROOT="$(pwd)"
fi
export REPO_ROOT

if [ -f "$CHANGE_ID" ]; then
  STATE_YAML="$(cd "$(dirname "$CHANGE_ID")" && pwd)/$(basename "$CHANGE_ID")"
else
  SLUG="$(echo "$CHANGE_ID" | tr '[:upper:]' '[:lower:]')"
  STATE_YAML="$(bash "$METRICS_DIR/resolve-state-yaml.sh" "$SLUG" "$REPO_ROOT")" || {
    echo "ERROR: cannot locate state.yaml for '$CHANGE_ID'" >&2
    exit 1
  }
fi

if [ ! -f "$STATE_YAML" ]; then
  echo "ERROR: state.yaml not found: $STATE_YAML" >&2
  exit 1
fi

PYTHONPATH="${_WORKTREE_ROOT}:${PYTHONPATH:-}" \
  python3 -m orchestrator_next.workflow_learner_cmd "$STATE_YAML"
exit $?
