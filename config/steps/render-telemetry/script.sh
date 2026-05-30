#!/usr/bin/env bash
# render-telemetry — DuckDB metrics dashboard (operator workflow step).
#
# Params (contract.yaml): TELEMETRY_SCOPE, TELEMETRY_FLEET, TELEMETRY_*_LIMIT
# Override at invoke time by exporting the same env var names.
set -uo pipefail

REPO_ROOT="${REPO_ROOT:-${ORCHESTRATOR_REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}}"
SCOPE="${TELEMETRY_SCOPE:-recent}"
FLEET_FLAG=""
[ "${TELEMETRY_FLEET:-}" = "1" ] && FLEET_FLAG="--fleet"
FEATURES_LIMIT="${TELEMETRY_FEATURES_LIMIT:-5}"
TREND_LIMIT="${TELEMETRY_TREND_LIMIT:-10}"
HOTSPOTS_LIMIT="${TELEMETRY_HOTSPOTS_LIMIT:-10}"

_ORCH_HOME="${ORCHESTRATOR_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
METRICS_SH=""
for candidate in \
  "$_ORCH_HOME/orchestrator_next/scripts/metrics/metrics-query.sh" \
  "$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../orchestrator_next/scripts/metrics" && pwd)/metrics-query.sh"; do
  if [ -f "$candidate" ]; then
    METRICS_SH="$candidate"
    break
  fi
done

if [ -z "$METRICS_SH" ]; then
  echo "No telemetry data (metrics-query.sh not found)." >&2
  exit 1
fi

REPO_NAME="$(basename "$REPO_ROOT")"
LIMIT_ARGS=()
[ "$SCOPE" = "recent" ] && LIMIT_ARGS=(--limit "$FEATURES_LIMIT")

_run() {
  local extra=()
  [ -z "$FLEET_FLAG" ] && extra=(--repo "$REPO_ROOT")
  REPO_ROOT="$REPO_ROOT" bash "$METRICS_SH" "${extra[@]}" "$@" $FLEET_FLAG 2>/dev/null || true
}

echo "═══════════════════════════════════════════════════"
echo "  WORKFLOW TELEMETRY — $REPO_NAME"
echo "  Scope: $SCOPE${FLEET_FLAG:+ (fleet)}"
echo "═══════════════════════════════════════════════════"
echo ""

section() {
  echo "$1"
  echo "─────────────────────────────────────────────────"
}

RECENT=$(_run recent-features "${LIMIT_ARGS[@]}")
if [ -n "$RECENT" ]; then
  section "RECENT FEATURES"
  echo "$RECENT"
  echo ""
else
  echo "No archived metrics in DuckDB for this scope."
  echo "Complete a feature workflow or run orchestrator_next/scripts/metrics/register-repo.sh to backfill archives."
  echo ""
  exit 0
fi

COST=$(_run cost-trend --limit "$TREND_LIMIT")
[ -n "$COST" ] && section "COST TREND" && echo "$COST" && echo ""

QUALITY=$(_run quality-trend --limit "$TREND_LIMIT")
[ -n "$QUALITY" ] && section "QUALITY TREND" && echo "$QUALITY" && echo ""

RETRIES=$(_run retry-hotspots --limit "$HOTSPOTS_LIMIT")
[ -n "$RETRIES" ] && section "RETRY HOTSPOTS" && echo "$RETRIES" && echo ""

STEPS=$(_run step-cost-hotspots --limit "$HOTSPOTS_LIMIT")
[ -n "$STEPS" ] && section "STEP COST HOTSPOTS" && echo "$STEPS" && echo ""

echo "═══════════════════════════════════════════════════"
exit 0
