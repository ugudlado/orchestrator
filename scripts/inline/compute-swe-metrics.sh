#!/usr/bin/env bash
# compute-swe-metrics.sh — Thin projection over `orchestrator metrics --format json`.
#
# Usage: compute-swe-metrics.sh <state_dir>
#
# Reads:  <state_dir>/state.yaml (for change_id only)
# Writes: metrics block to stdout as YAML (for injection into state.yaml)
#
# Data source: DuckDB via `orchestrator metrics` — no JSONL parsing, no git log,
# no tasks.md reads. All aggregation is done inside the orchestrator CLI.

set -euo pipefail

STATE_DIR="${1:?Usage: compute-swe-metrics.sh <state_dir>}"
STATE_YAML="$STATE_DIR/state.yaml"

if [[ ! -f "$STATE_YAML" ]]; then
  echo "ERROR: state.yaml not found at $STATE_YAML" >&2
  exit 1
fi

# Resolve ORCHESTRATOR_HOME: env var → git rev-parse fallback
ORCHESTRATOR_HOME="${ORCHESTRATOR_HOME:-$(git rev-parse --show-toplevel 2>/dev/null || echo "")}"
if [[ -z "$ORCHESTRATOR_HOME" ]]; then
  echo "ERROR: ORCHESTRATOR_HOME is not set and git rev-parse failed" >&2
  exit 1
fi

CHANGE_ID=$(yq -r '.change_id' "$STATE_YAML")
if [[ -z "$CHANGE_ID" || "$CHANGE_ID" == "null" ]]; then
  echo "ERROR: change_id not found in $STATE_YAML" >&2
  exit 1
fi

# Shell out to orchestrator metrics — single invocation, no parallel reads
set +e
JSON=$("$ORCHESTRATOR_HOME/bin/orchestrator" metrics \
  --change-id "$CHANGE_ID" --format json 2>"${TMPDIR:-/tmp}/csm-err-$$.txt")
CLI_EXIT=$?
set -e

if [[ "$CLI_EXIT" -ne 0 ]]; then
  echo "ERROR: orchestrator metrics failed for change_id=$CHANGE_ID" >&2
  cat "${TMPDIR:-/tmp}/csm-err-$$.txt" >&2
  rm -f "${TMPDIR:-/tmp}/csm-err-$$.txt"
  exit 1
fi
rm -f "${TMPDIR:-/tmp}/csm-err-$$.txt"

# Add provenance field and emit as YAML under metrics: key
TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
echo "$JSON" | python3 -c "
import sys, json, yaml
d = json.load(sys.stdin)
d['source'] = 'duckdb@$TS'
print(yaml.safe_dump({'metrics': d}, sort_keys=True, default_flow_style=False), end='')
"
