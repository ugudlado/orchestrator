#!/usr/bin/env bash
# gather-learn-metrics — DuckDB inputs for workflow-learner (operator workflow step).
#
# Params (contract.yaml): LEARN_SCOPE, LEARN_*_LIMIT
# Override at invoke time by exporting the same env var names.
set -uo pipefail

REPO_ROOT="${REPO_ROOT:-${ORCHESTRATOR_REPO_ROOT:-$(pwd)}}"
SCOPE="${LEARN_SCOPE:-all}"
STATE="${ORCHESTRATOR_STATE_YAML_PATH:-}"
RETRY_LIMIT="${LEARN_RETRY_HOTSPOTS_LIMIT:-10}"
RECENT_LIMIT="${LEARN_RECENT_FEATURES_LIMIT:-10}"
QUALITY_LIMIT="${LEARN_QUALITY_TREND_LIMIT:-5}"

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
  printf '%s\n' '{"learn_metrics":{"status":"unavailable","reason":"metrics-query.sh missing"}}'
  exit 0
fi

_run() {
  local extra=()
  case "$1" in
    retry-hotspots) extra=(--fleet) ;;
    *) extra=(--repo "$REPO_ROOT") ;;
  esac
  REPO_ROOT="$REPO_ROOT" bash "$METRICS_SH" "${extra[@]}" "$@" 2>/dev/null || true
}

export LM_SCOPE="$SCOPE"
export LM_STATE="$STATE"
export LM_RETRY="$(_run retry-hotspots --fleet --limit "$RETRY_LIMIT")"
export LM_CYCLE="$(_run cycle-count)"
export LM_RECENT="$(_run recent-features --limit "$RECENT_LIMIT")"
export LM_QUALITY="$(_run quality-trend --limit "$QUALITY_LIMIT")"

python3 <<'PY'
import json, os
print(json.dumps({
    "learn_metrics": {
        "scope": os.environ.get("LM_SCOPE", "all"),
        "state_yaml_path": os.environ.get("LM_STATE", ""),
        "retry_hotspots_csv": os.environ.get("LM_RETRY", "").strip(),
        "cycle_count_csv": os.environ.get("LM_CYCLE", "").strip(),
        "recent_features_csv": os.environ.get("LM_RECENT", "").strip(),
        "quality_trend_csv": os.environ.get("LM_QUALITY", "").strip(),
    },
}))
PY
exit 0
