#!/usr/bin/env bash
# read-sub-state-metrics.sh — Thin projection over `orchestrator metrics --format json`.
# Usage: read-sub-state-metrics.sh <slug>
# Emits narrow YAML (tokens.total, duration_ms, churn.files_changed) for autopilot rollup.
set -euo pipefail

SLUG="${1:?Usage: read-sub-state-metrics.sh <slug>}"

# Resolve ORCHESTRATOR_HOME: env var → git rev-parse fallback
ORCHESTRATOR_HOME="${ORCHESTRATOR_HOME:-$(git rev-parse --show-toplevel 2>/dev/null || echo "")}"
if [[ -z "$ORCHESTRATOR_HOME" ]]; then
  echo "ERROR: ORCHESTRATOR_HOME is not set and git rev-parse failed" >&2
  exit 1
fi

# Shell out once — all repo_root/change_id resolution happens inside orchestrator
set +e
JSON=$("$ORCHESTRATOR_HOME/bin/orchestrator" metrics \
  --change-id "$SLUG" --format json 2>"${TMPDIR:-/tmp}/rsm-err-$$.txt")
CLI_EXIT=$?
set -e

if [[ "$CLI_EXIT" -ne 0 ]]; then
  echo "ERROR: orchestrator metrics failed for slug=$SLUG" >&2
  cat "${TMPDIR:-/tmp}/rsm-err-$$.txt" >&2
  rm -f "${TMPDIR:-/tmp}/rsm-err-$$.txt"
  exit 1
fi
rm -f "${TMPDIR:-/tmp}/rsm-err-$$.txt"

# Project to narrow shape: tokens.total, duration_ms (step sum), churn.files_changed
echo "$JSON" | python3 -c "
import sys, json
d = json.load(sys.stdin)
tok   = (d.get('tokens') or {}).get('total') or 0
dur   = sum((v.get('duration_ms') or 0) for v in (d.get('per_step') or {}).values())
churn = (d.get('churn') or {}).get('files_changed') or 0
print(f'metrics:\n  tokens:\n    total: {tok}\n  duration_ms: {dur}\n  churn:\n    files_changed: {churn}')
"
