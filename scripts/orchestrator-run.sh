#!/usr/bin/env bash
# orchestrator-run.sh — ticket-driven entry for the shell workflow loop
#
# Usage:
#   orchestrator run <ticket-id> [--schema feature|bugfix] [--repo PATH] [flag=value ...]
#
# Resolves repo root, consults ticket status, seeds state.yaml when needed,
# then execs run-workflow.sh. Use this instead of seed-state + run-workflow
# when not driving the workflow from chat (/orchestrate).
#
# Exit codes: same as run-workflow.sh (1=complete, 2=blocked, 3–7 errors)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_WORKFLOW="$SCRIPT_DIR/run-workflow.sh"
SEED_STATE="${ORCHESTRATOR_HOME:-}/skills/orchestrate/scripts/seed-state.sh"
# When invoked from a dev checkout, prefer repo-local seed + scripts
_WORKTREE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
if [ -f "$_WORKTREE_ROOT/skills/orchestrate/scripts/seed-state.sh" ]; then
  SEED_STATE="$_WORKTREE_ROOT/skills/orchestrate/scripts/seed-state.sh"
fi

TICKET_ID=""
SCHEMA="feature"
REPO_ROOT_ARG=""
FLAG_OVERRIDES=()

usage() {
  echo "Usage: orchestrator run <ticket-id> [--schema feature|bugfix] [--repo PATH] [flag=value ...]" >&2
  echo "  Examples:" >&2
  echo "    orchestrator run ORC-83" >&2
  echo "    orchestrator run HL-287 --schema bugfix" >&2
  echo "    orchestrator run 42 --repo /path/to/app worktree=true" >&2
  exit 7
}

while [ $# -gt 0 ]; do
  case "$1" in
    --schema)
      [ $# -ge 2 ] || usage
      SCHEMA="$2"
      shift 2
      ;;
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
      if [ -z "$TICKET_ID" ]; then
        TICKET_ID="$1"
        shift
      elif [[ "$1" == *"="* ]]; then
        FLAG_OVERRIDES+=("$1")
        shift
      else
        echo "ERROR: unexpected argument: $1" >&2
        usage
      fi
      ;;
  esac
done

[ -n "$TICKET_ID" ] || usage

if [ -n "$REPO_ROOT_ARG" ]; then
  REPO_ROOT="$(cd "$REPO_ROOT_ARG" && pwd)"
else
  REPO_ROOT="${REPO_ROOT:-$(git -C "$_WORKTREE_ROOT" rev-parse --show-toplevel 2>/dev/null || true)}"
  if [ -z "$REPO_ROOT" ] || [ ! -f "$REPO_ROOT/spec/project.yaml" ]; then
    REPO_ROOT="$(pwd)"
    if [ ! -f "$REPO_ROOT/spec/project.yaml" ]; then
      echo "ERROR: spec/project.yaml not found (use --repo PATH or run from target repo)" >&2
      exit 7
    fi
  fi
fi

if [ ! -f "$REPO_ROOT/spec/project.yaml" ]; then
  echo "ERROR: spec/project.yaml not found under $REPO_ROOT" >&2
  exit 7
fi

if [ ! -x "$RUN_WORKFLOW" ]; then
  echo "ERROR: run-workflow.sh not found at $RUN_WORKFLOW" >&2
  exit 7
fi

export REPO_ROOT
export ORCHESTRATOR_HOME="${ORCHESTRATOR_HOME:-$HOME/.config/orchestrator}"
if [ -d "$_WORKTREE_ROOT/config" ]; then
  export ORCHESTRATOR_HOME="$_WORKTREE_ROOT"
fi

WORKFLOW_STATE_DIR="${WORKFLOW_STATE_DIR:-$REPO_ROOT/spec/changes}"
WORKTREE_BASE_DIR="${WORKTREE_BASE_DIR:-$HOME/code/feature_worktrees}"
TICKET_SLUG="$(echo "$TICKET_ID" | tr '[:upper:]' '[:lower:]')"
DEFAULT_STATE="$WORKFLOW_STATE_DIR/$TICKET_SLUG/state.yaml"

# state.yaml may live in repo_root (worktree=false) or under the feature worktree.
resolve_state_yaml() {
  local slug="$1"
  if [ -f "$WORKFLOW_STATE_DIR/$slug/state.yaml" ]; then
    echo "$WORKFLOW_STATE_DIR/$slug/state.yaml"
    return 0
  fi
  local wt_state="$WORKTREE_BASE_DIR/$slug/spec/changes/$slug/state.yaml"
  if [ -f "$wt_state" ]; then
    echo "$wt_state"
    return 0
  fi
  return 1
}

TICKET_CHECK="$SCRIPT_DIR/ticket-status-check.sh"
if [ ! -f "$TICKET_CHECK" ]; then
  echo "ERROR: ticket-status-check.sh not found" >&2
  exit 7
fi

TICKET_JSON=$(bash "$TICKET_CHECK" "$TICKET_ID" "$REPO_ROOT" 2>/dev/null || echo '{"action":"skip"}')
TICKET_ACTION=$(echo "$TICKET_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('action','skip'))" 2>/dev/null || echo "skip")

case "$TICKET_ACTION" in
  halt)
    TICKET_REASON=$(echo "$TICKET_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('reason',''))" 2>/dev/null || echo "")
    echo "ERROR: Ticket check halted: $TICKET_REASON" >&2
    echo "$TICKET_JSON" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for item in d.get('checklist') or []:
    print('  -', item)
" 2>/dev/null || true
    exit 6
    ;;
esac

STATE_YAML=""
if [ "$TICKET_ACTION" = "resume" ]; then
  STATE_YAML=$(echo "$TICKET_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('state_yaml',''))" 2>/dev/null || echo "")
fi

if [ -z "$STATE_YAML" ] || [ ! -f "$STATE_YAML" ]; then
  STATE_YAML="$(resolve_state_yaml "$TICKET_SLUG" 2>/dev/null || true)"
fi

if [ ! -f "${STATE_YAML:-}" ]; then
  if [ ! -f "$SEED_STATE" ]; then
    echo "ERROR: no state.yaml for $TICKET_SLUG and seed-state.sh missing at $SEED_STATE" >&2
    exit 7
  fi
  echo "Seeding workflow: slug=$TICKET_SLUG schema=$SCHEMA" >&2
  # shellcheck disable=SC2086
  bash "$SEED_STATE" "$TICKET_SLUG" "$SCHEMA" "${FLAG_OVERRIDES[@]}" || exit 7
  STATE_YAML="$(resolve_state_yaml "$TICKET_SLUG")" || STATE_YAML=""
fi

if [ ! -f "$STATE_YAML" ]; then
  echo "ERROR: state.yaml not found at $STATE_YAML after seed" >&2
  exit 7
fi

echo "Running shell workflow: ticket=$TICKET_ID state=$STATE_YAML" >&2
exec bash "$RUN_WORKFLOW" "$STATE_YAML" "$TICKET_ID"
