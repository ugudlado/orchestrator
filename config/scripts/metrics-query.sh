#!/usr/bin/env bash
# metrics-query.sh — Named-query wrapper over metrics.duckdb
#
# Usage:
#   metrics-query.sh <query-id> [--repo <path>] [--fleet] [--limit <N>]
#
# Query IDs: cost-trend, retry-hotspots, cycle-count, quality-trend, recent-features
#
# Environment:
#   ORCHESTRATOR_HOME  Default: $HOME/.orchestrator
#   METRICS_DB         Default: $ORCHESTRATOR_HOME/metrics.duckdb

set -uo pipefail

# ── Env resolution ───────────────────────────────────────────────────────────
ORCHESTRATOR_HOME="${ORCHESTRATOR_HOME:-$HOME/.orchestrator}"
METRICS_DB="${METRICS_DB:-$ORCHESTRATOR_HOME/metrics.duckdb}"

# ── Arg parsing ──────────────────────────────────────────────────────────────
QUERY_ID=""
REPO_PATH=""
FLEET=false
LIMIT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      REPO_PATH="$2"
      shift 2
      ;;
    --fleet)
      FLEET=true
      shift
      ;;
    --limit)
      LIMIT="$2"
      shift 2
      ;;
    -*)
      shift
      ;;
    *)
      if [[ -z "$QUERY_ID" ]]; then
        QUERY_ID="$1"
      fi
      shift
      ;;
  esac
done

# ── Preflight ────────────────────────────────────────────────────────────────
if ! command -v duckdb >/dev/null 2>&1; then
  exit 1
fi

if [[ ! -f "$METRICS_DB" ]]; then
  exit 1
fi

# ── Scope clause ─────────────────────────────────────────────────────────────
if [[ "$FLEET" == true ]]; then
  SCOPE="1=1"
else
  if [[ -z "$REPO_PATH" ]]; then
    REPO_PATH="$PWD"
  fi
  SCOPE="repo_root = '${REPO_PATH//\'/\'\'}'"
fi

# ── Limit clause ─────────────────────────────────────────────────────────────
if [[ -n "$LIMIT" ]]; then
  LIMIT_CLAUSE=" LIMIT ${LIMIT}"
else
  LIMIT_CLAUSE=""
fi

# ── Build SQL ────────────────────────────────────────────────────────────────
case "$QUERY_ID" in
  cost-trend)
    SQL="SELECT change_id, completed_at, json_extract(payload_json, '$.metrics.cost_usd') AS cost FROM features WHERE ${SCOPE} ORDER BY completed_at DESC${LIMIT_CLAUSE}"
    ;;
  quality-trend)
    SQL="SELECT change_id, completed_at, json_extract(payload_json, '$.metrics.quality_score') AS quality_score FROM features WHERE ${SCOPE} ORDER BY completed_at DESC${LIMIT_CLAUSE}"
    ;;
  retry-hotspots)
    SQL="SELECT json_extract(s.value, '$.step_id') AS step_id, r.value AS reason, COUNT(DISTINCT f.change_id) AS feature_count, SUM(CAST(json_extract(s.value, '$.retries') AS INTEGER)) AS total_retries FROM features f, json_each(json_extract(f.payload_json, '$.step_history')) s, json_each(json_extract(s.value, '$.retry_reasons')) r WHERE ${SCOPE} GROUP BY step_id, reason ORDER BY total_retries DESC${LIMIT_CLAUSE}"
    ;;
  cycle-count)
    SQL="SELECT COUNT(*) AS cycle_count FROM features WHERE ${SCOPE}"
    ;;
  recent-features)
    SQL="SELECT change_id, status, completed_at, payload_json FROM features WHERE ${SCOPE} ORDER BY completed_at DESC${LIMIT_CLAUSE}"
    ;;
  *)
    exit 2
    ;;
esac

# ── Execute ──────────────────────────────────────────────────────────────────
OUTPUT=$(duckdb -csv "$METRICS_DB" "$SQL" 2>/dev/null)

# Strip header line to check if data rows exist
DATA_ROWS=$(printf '%s' "$OUTPUT" | tail -n +2)

if [[ -z "$DATA_ROWS" ]]; then
  exit 1
fi

printf '%s\n' "$OUTPUT"
