#!/usr/bin/env bash
# complete-feature-teardown.sh — remove feature worktree after complete phase.
#
# Run from orchestrator complete after merge. Reads worktree_path / branch /
# repo_root from archived (or active) state.yaml via read-state-env.sh.
#
# Usage:
#   complete-feature-teardown.sh <change-id>
#   complete-feature-teardown.sh spec/changes/archive/orc-85/state.yaml
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)}"
COMPLETE_DIR="${ORCHESTRATOR_HOME:+$ORCHESTRATOR_HOME/orchestrator_next/scripts/complete}"
COMPLETE_DIR="${COMPLETE_DIR:-$SCRIPT_DIR/complete}"
READ_STATE_ENV="$SCRIPT_DIR/lib/read-state-env.sh"
ARG="${1:-}"

if [ -z "$ARG" ]; then
  echo "Usage: complete-feature-teardown.sh <change-id|state.yaml>" >&2
  exit 1
fi

STATE_YAML=""
if [ -f "$ARG" ]; then
  STATE_YAML="$(cd "$(dirname "$ARG")" && pwd)/$(basename "$ARG")"
else
  SLUG="$(echo "$ARG" | tr '[:upper:]' '[:lower:]')"
  WT_BASE="${WORKTREE_BASE_DIR:-$HOME/code/feature_worktrees}"
  for candidate in \
    "$REPO_ROOT/spec/changes/archive/$SLUG/state.yaml" \
    "$REPO_ROOT/spec/changes/archive"/*"-$SLUG"/state.yaml" \
    "$WT_BASE/$SLUG/spec/changes/archive/$SLUG/state.yaml" \
    "$WT_BASE/$SLUG/spec/changes/$SLUG/state.yaml" \
    "$REPO_ROOT/spec/changes/$SLUG/state.yaml"; do
    if [ -f "$candidate" ]; then
      STATE_YAML="$candidate"
      break
    fi
  done
fi

if [ -z "$STATE_YAML" ] || [ ! -f "$STATE_YAML" ]; then
  echo "ERROR: no state.yaml for $ARG" >&2
  exit 1
fi

WORKTREE_PATH=""
BRANCH=""
# shellcheck source=lib/read-state-env.sh
source "$READ_STATE_ENV"
read_state_env "$STATE_YAML" WORKTREE_PATH BRANCH REPO_ROOT

# ORC-108: worktree is unconditional, so worktree_path presence — not a flag —
# is the signal that there is a worktree to remove.
if [ -z "$WORKTREE_PATH" ]; then
  echo '{"removed": false, "reason": "no worktree_path in state"}'
  exit 0
fi

WORKTREE_PATH="${WORKTREE_PATH/#\~/$HOME}"
export REPO_ROOT WORKTREE_PATH BRANCH
exec bash "$COMPLETE_DIR/remove-worktree.sh"
