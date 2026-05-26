#!/usr/bin/env bash
# complete-feature-teardown.sh — remove feature worktree after complete phase.
#
# Run from /complete-feature after orchestrate --phase complete. Reads
# worktree_path / branch / repo_root from archived (or active) state.yaml.
#
# Usage:
#   complete-feature-teardown.sh <change-id>
#   complete-feature-teardown.sh spec/changes/archive/orc-85/state.yaml
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)}"
INLINE_DIR="${ORCHESTRATOR_HOME:-$REPO_ROOT}/config/scripts/inline"
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
    "$REPO_ROOT/spec/changes/archive"/*"-$SLUG"/state.yaml \
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
WORKTREE=""
# shellcheck source=config/scripts/inline/_read_state_env.sh
source "$INLINE_DIR/_read_state_env.sh"
read_state_env "$STATE_YAML" WORKTREE_PATH BRANCH WORKTREE REPO_ROOT

if [[ "$WORKTREE" != "true" && "$WORKTREE" != "True" ]]; then
  echo '{"removed": false, "reason": "worktree flag false"}'
  exit 0
fi

if [ -z "$WORKTREE_PATH" ]; then
  echo '{"removed": false, "reason": "no worktree_path in state"}'
  exit 0
fi

WORKTREE_PATH="${WORKTREE_PATH/#\~/$HOME}"
export REPO_ROOT WORKTREE_PATH BRANCH
export STATE_YAML_PATH=""
exec bash "$INLINE_DIR/remove-worktree.sh"
