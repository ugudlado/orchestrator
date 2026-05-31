#!/usr/bin/env bash
# merge-to-main — merge feature branch to default branch.
set -euo pipefail

: "${REPO_ROOT:?orchestrator: REPO_ROOT required}"

STATE_YAML="${ORCHESTRATOR_STATE_YAML_PATH:-${STATE_YAML_PATH:?orchestrator: state yaml path required}}"

_read_state_field() {
  python3 -c "
import sys, yaml
raw = yaml.safe_load(open('$STATE_YAML')) or {}
print(raw.get('$1') or '')
" 2>/dev/null || true
}

BRANCH="${BRANCH:-$(_read_state_field branch)}"
CHANGE_ID="${CHANGE_ID:-$(_read_state_field change_id)}"

MERGE_SCRIPT="$(dirname "$0")/../../../../orchestrator_next/scripts/complete/merge-to-main.sh"

REPO_ROOT="$REPO_ROOT" BRANCH="$BRANCH" CHANGE_ID="$CHANGE_ID" bash "$MERGE_SCRIPT"
