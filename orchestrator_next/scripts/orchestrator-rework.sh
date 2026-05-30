#!/usr/bin/env bash
# orchestrator-rework.sh — QA rework: ticket back to In Progress (branch kept)
#
# Usage (workflow: config/workflows/rework.yaml):
#   orchestrator rework <change-id-or-state-yaml> [--repo PATH]
#
# Resolves state.yaml via resolve-state-yaml (live, archive, worktree-aware),
# then runs the ticket-rework step from the rework workflow config.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_WORKTREE_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
METRICS_DIR="$SCRIPT_DIR/metrics"

CHANGE_ID=""
REPO_ROOT_ARG=""

usage() {
  echo "Usage: orchestrator rework <change-id-or-state-yaml> [--repo PATH]" >&2
  exit 7
}

while [ $# -gt 0 ]; do
  case "$1" in
    --repo)
      [ $# -ge 2 ] || usage
      REPO_ROOT_ARG="$2"
      shift 2
      ;;
    --help|-h)
      usage
      ;;
    -*)
      echo "ERROR: unknown option: $1" >&2
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

if [ -n "$REPO_ROOT_ARG" ]; then
  REPO_ROOT="$(cd "$REPO_ROOT_ARG" && pwd)"
else
  REPO_ROOT="${REPO_ROOT:-$(git -C "$_WORKTREE_ROOT" rev-parse --show-toplevel 2>/dev/null || true)}"
  if [ -z "$REPO_ROOT" ] || [ ! -f "$REPO_ROOT/spec/project.yaml" ]; then
    REPO_ROOT="$(pwd)"
  fi
fi

if [ ! -f "$REPO_ROOT/spec/project.yaml" ]; then
  echo "ERROR: spec/project.yaml not found under $REPO_ROOT" >&2
  exit 7
fi

export REPO_ROOT
export ORCHESTRATOR_HOME="${ORCHESTRATOR_HOME:-$HOME/.config/orchestrator}"
if [ -d "$_WORKTREE_ROOT/config" ]; then
  export ORCHESTRATOR_HOME="$_WORKTREE_ROOT"
fi

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

echo "Running rework: change=$CHANGE_ID state=$STATE_YAML" >&2
PYTHONPATH="${_WORKTREE_ROOT}:${PYTHONPATH:-}" \
  python3 -m orchestrator_next.rework "$STATE_YAML"
exit $?
