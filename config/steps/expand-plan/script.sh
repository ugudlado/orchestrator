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

exec python3 "${REPO_ROOT}/bin/orchestrator" expand-plan "$STATE"
