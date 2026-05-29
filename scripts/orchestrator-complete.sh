#!/usr/bin/env bash
# orchestrator-complete.sh — run complete phase only for an active feature
#
# Usage:
#   orchestrator complete <ticket-id> [--repo PATH] [--no-teardown] [flag=value ...]
#
# Order on success:
#   1. Complete-phase steps (through complete-workflow → archive on feature branch)
#   2. Merge to default branch (when flags.merge_to_main) — failure stops here
#   3. Remove feature worktree (default; --no-teardown to keep)
#
# Exit codes: same as run-workflow.sh (1=complete, 2=blocked, 3–7 errors);
# merge failures propagate as non-zero (worktree is not removed).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_WORKFLOW="$SCRIPT_DIR/run-workflow.sh"
TEARDOWN="$SCRIPT_DIR/complete-feature-teardown.sh"
_INLINE_DIR="${ORCHESTRATOR_HOME:-$(cd "$SCRIPT_DIR/.." && pwd)}/scripts/inline"
# shellcheck source=lib/agent-routes.sh
source "$SCRIPT_DIR/lib/agent-routes.sh"

TICKET_ID=""
REPO_ROOT_ARG=""
RUN_TEARDOWN=true
FLAG_OVERRIDES=()

usage() {
  echo "Usage: orchestrator complete <ticket-id> [--repo PATH] [--no-teardown] [flag=value ...]" >&2
  exit 7
}

while [ $# -gt 0 ]; do
  case "$1" in
    --repo)
      [ $# -ge 2 ] || usage
      REPO_ROOT_ARG="$2"
      shift 2
      ;;
    --no-teardown)
      RUN_TEARDOWN=false
      shift
      ;;
    --teardown)
      echo "WARNING: --teardown is default; use --no-teardown to keep the worktree" >&2
      shift
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

_WORKTREE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
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
  _INLINE_DIR="$_WORKTREE_ROOT/scripts/inline"
fi

WORKFLOW_STATE_DIR="${WORKFLOW_STATE_DIR:-$REPO_ROOT/spec/changes}"
WORKTREE_BASE_DIR="${WORKTREE_BASE_DIR:-$HOME/code/feature_worktrees}"
TICKET_SLUG="$(echo "$TICKET_ID" | tr '[:upper:]' '[:lower:]')"

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

resolve_archived_state_yaml() {
  local slug="$1"
  local wt_archived="$WORKTREE_BASE_DIR/$slug/spec/changes/archive/$slug/state.yaml"
  if [ -f "$wt_archived" ]; then
    echo "$wt_archived"
    return 0
  fi
  if [ -f "$WORKFLOW_STATE_DIR/archive/$slug/state.yaml" ]; then
    echo "$WORKFLOW_STATE_DIR/archive/$slug/state.yaml"
    return 0
  fi
  local dated
  for dated in "$WORKFLOW_STATE_DIR/archive"/*"-$slug"/state.yaml; do
    if [ -f "$dated" ]; then
      echo "$dated"
      return 0
    fi
  done
  return 1
}

STATE_YAML="$(resolve_state_yaml "$TICKET_SLUG" 2>/dev/null || true)"
if [ -z "$STATE_YAML" ] || [ ! -f "$STATE_YAML" ]; then
  echo "ERROR: no state.yaml for $TICKET_SLUG (start workflow with orchestrator run first)" >&2
  exit 7
fi

if [[ "$STATE_YAML" == *"/archive/"* ]]; then
  echo "ERROR: $TICKET_SLUG is already archived at $STATE_YAML" >&2
  exit 1
fi

_PREPARE=$(PYTHONPATH="${_WORKTREE_ROOT}:${PYTHONPATH:-}" \
  python3 -m orchestrator_next.complete_phase "$STATE_YAML" 2>&1) || {
  echo "$_PREPARE" >&2
  exit 2
}
echo "$_PREPARE" | head -1 >&2

if [ ! -x "$RUN_WORKFLOW" ]; then
  echo "ERROR: run-workflow.sh not found at $RUN_WORKFLOW" >&2
  exit 7
fi

echo "Running complete phase: ticket=$TICKET_ID state=$STATE_YAML" >&2
set +e
bash "$RUN_WORKFLOW" "$STATE_YAML" "$TICKET_ID"
_RC=$?
set -e

if [ "$_RC" -ne 1 ]; then
  exit "$_RC"
fi

ARCHIVED_STATE="$(resolve_archived_state_yaml "$TICKET_SLUG" 2>/dev/null || true)"
if [ -z "$ARCHIVED_STATE" ] || [ ! -f "$ARCHIVED_STATE" ]; then
  echo "ERROR: archived state.yaml not found for $TICKET_SLUG after complete-workflow" >&2
  exit 7
fi

MERGE_TO_MAIN=$(python3 - "$ARCHIVED_STATE" <<'PY'
import sys, yaml
raw = yaml.safe_load(open(sys.argv[1])) or {}
flags = raw.get("flags") or {}
print("true" if flags.get("merge_to_main") else "false")
PY
)
# ORC-108: worktree is unconditional — its presence is keyed off worktree_path
# in state, not a flag (which no longer exists).
USE_WORKTREE=$(python3 - "$ARCHIVED_STATE" <<'PY'
import sys, yaml
raw = yaml.safe_load(open(sys.argv[1])) or {}
print("true" if (raw.get("worktree_path") or "").strip() else "false")
PY
)

if [ "$MERGE_TO_MAIN" = true ]; then
  # Worktree-first learn-cycle: if learn-cycle modified global config/rules,
  # commit those changes on the feature branch before merging to main.
  if [ "$USE_WORKTREE" = true ]; then
    WT_DIR="$WORKTREE_BASE_DIR/$TICKET_SLUG"
    BRANCH="$(python3 - "$ARCHIVED_STATE" <<'PY'
import sys, yaml
raw = yaml.safe_load(open(sys.argv[1])) or {}
print(raw.get("branch") or "")
PY
)"
    COMMIT_HELPER="$_INLINE_DIR/commit-worktree-learn-updates.sh"
    if [ -x "$COMMIT_HELPER" ]; then
      # --require-clean: a dirty worktree must not be merged to main.
      bash "$COMMIT_HELPER" "$WT_DIR" "$TICKET_SLUG" "$BRANCH" --require-clean
    fi
  fi

  echo "Merging $TICKET_SLUG branch to default..." >&2
  set +e
  _MERGE_OUT=$(STATE_YAML_PATH="$ARCHIVED_STATE" REPO_ROOT="$REPO_ROOT" \
    bash "$_INLINE_DIR/merge-to-main.sh" 2>&1)
  _MERGE_RC=$?
  set -e
  if [ "$_MERGE_RC" -ne 0 ]; then
    echo "$_MERGE_OUT" >&2
    echo "ERROR: merge failed; worktree kept for conflict resolution" >&2
    exit "$_MERGE_RC"
  fi
  echo "$_MERGE_OUT" | tail -1 >&2
fi

if [ "$RUN_TEARDOWN" = true ] && [ "$USE_WORKTREE" = true ]; then
  if [ -x "$TEARDOWN" ]; then
    echo "Removing feature worktree..." >&2
    bash "$TEARDOWN" "$TICKET_SLUG"
  fi
fi

exit 1
