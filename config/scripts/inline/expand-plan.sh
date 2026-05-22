#!/usr/bin/env bash
# expand-plan.sh — read tasks.yaml and append task-nodes to workflow_plan.
#
# Invoked as an inline step after design-and-draft-artifacts completes.
# Env inputs: STATE_YAML_PATH
# Exit 0: success (task-nodes appended or already present).
# Exit 1: error (tasks.yaml missing, validation failure, cycle detected).

set -uo pipefail

STATE="${STATE_YAML_PATH:-}"

if [[ -z "$STATE" ]]; then
  echo "Error: STATE_YAML_PATH not set" >&2
  exit 1
fi

if [[ ! -f "$STATE" ]]; then
  echo "Error: state.yaml not found at: $STATE" >&2
  exit 1
fi

# Resolve the orchestrator binary from the repo root.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
ORCHESTRATOR_BIN="$REPO_ROOT/bin/orchestrator"

exec python3 "$ORCHESTRATOR_BIN" expand-plan "$STATE"
