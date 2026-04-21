#!/usr/bin/env bash
# read-sub-state-metrics.sh — Narrow projection from feature_report view.
# Usage: read-sub-state-metrics.sh <slug>
# Emits narrow YAML (tokens.total, duration_ms, churn.files_changed) for autopilot rollup.
#
# Data source: DuckDB feature_report view — no Python intermediary, no CLI shell-out.
# Rewritten as part of Phase 3 (report-views-retire-cli).
set -uo pipefail

SLUG="${1:?Usage: read-sub-state-metrics.sh <slug>}"

# Slug-guard: SLUG must match ^[a-z0-9][a-z0-9-]*$ before embedding in SQL (NFR-2)
if ! echo "$SLUG" | grep -qE '^[a-z0-9][a-z0-9-]*$'; then
  echo "ERROR: slug '$SLUG' violates slug guard (^[a-z0-9][a-z0-9-]*$)" >&2
  exit 3
fi

# Resolve ORCHESTRATOR_HOME: env var → git rev-parse fallback
ORCHESTRATOR_HOME="${ORCHESTRATOR_HOME:-$(git rev-parse --show-toplevel 2>/dev/null || echo "")}"
if [[ -z "$ORCHESTRATOR_HOME" ]]; then
  echo "ERROR: ORCHESTRATOR_HOME is not set and git rev-parse failed" >&2
  exit 1
fi

# Resolve DB path: METRICS_DB env var → ORCHESTRATOR_HOME/metrics.duckdb
DB_PATH="${METRICS_DB:-$ORCHESTRATOR_HOME/metrics.duckdb}"

# Query feature_report for the three narrow columns only (FR-9, design § Components §4)
set +e
JSON=$(duckdb -readonly -json "$DB_PATH" \
  -c "SELECT total_tokens, duration_ms, files_changed FROM feature_report WHERE change_id = '$SLUG'" \
  2>"${TMPDIR:-/tmp}/rsm-err-$$.txt")
DUCK_EXIT=$?
set -e

if [[ "$DUCK_EXIT" -ne 0 ]]; then
  echo "ERROR: duckdb query failed for slug=$SLUG" >&2
  cat "${TMPDIR:-/tmp}/rsm-err-$$.txt" >&2
  rm -f "${TMPDIR:-/tmp}/rsm-err-$$.txt"
  exit 1
fi
rm -f "${TMPDIR:-/tmp}/rsm-err-$$.txt"

# Project to narrow YAML: tokens.total, duration_ms, churn.files_changed
echo "$JSON" | python3 -c "
import sys, json
rows = json.load(sys.stdin)
if not rows:
    sys.stderr.write('ERROR: no events for slug=$SLUG\n')
    sys.exit(1)
r = rows[0]
tok   = r.get('total_tokens') or 0
dur   = r.get('duration_ms') or 0
churn = r.get('files_changed') or 0
print(f'metrics:\n  tokens:\n    total: {tok}\n  duration_ms: {dur}\n  churn:\n    files_changed: {churn}')
"
