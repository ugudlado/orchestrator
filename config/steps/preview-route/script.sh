#!/usr/bin/env bash
# preview-route — wrap metrics/estimate-cost.sh; emit route_preview JSON.
# Non-blocking: unavailability is a normal outcome.
#
# Env: ORCHESTRATOR_WORKFLOW_DIR, ORCHESTRATOR_HOME, ORCHESTRATOR_CHANGE_ID, REPO_ROOT
# State lookup: resolve-state-yaml.sh → dirname(state.yaml) passed to estimator.

set -uo pipefail

WORKFLOW_DIR="${ORCHESTRATOR_WORKFLOW_DIR:-${WORKFLOW_DIR:-}}"
ORCHESTRATOR_HOME="${ORCHESTRATOR_HOME:-$HOME/.config/orchestrator}"
_ORCH_HOME="${ORCHESTRATOR_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
ESTIMATOR=""
for candidate in \
  "$_ORCH_HOME/orchestrator_next/scripts/metrics/estimate-cost.sh" \
  "$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../orchestrator_next/scripts/metrics" && pwd)/estimate-cost.sh"; do
  if [ -f "$candidate" ]; then
    ESTIMATOR="$candidate"
    break
  fi
done

if [ -z "$ESTIMATOR" ] || [ ! -f "$ESTIMATOR" ]; then
  printf '%s\n' "{\"route_preview\": {\"status\": \"estimate_unavailable\", \"reason\": \"estimator not found at $ESTIMATOR\"}}"
  exit 0
fi

if [ -z "$WORKFLOW_DIR" ] || [ ! -d "$WORKFLOW_DIR" ]; then
  printf '%s\n' "{\"route_preview\": {\"status\": \"estimate_unavailable\", \"reason\": \"workflow dir missing\"}}"
  exit 0
fi

TMPOUT=$(mktemp "${TMPDIR:-/tmp}/preview-route-out.XXXXXX")
TMPERR=$(mktemp "${TMPDIR:-/tmp}/preview-route-err.XXXXXX")

if [ -n "${ORCHESTRATOR_CHANGE_ID:-}" ] && [ -n "${REPO_ROOT:-$(git -C "$WORKFLOW_DIR" rev-parse --show-toplevel 2>/dev/null)}" ]; then
  RESOLVE_SCRIPT=""
  for candidate in \
    "$_ORCH_HOME/orchestrator_next/scripts/metrics/resolve-state-yaml.sh" \
    "$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../orchestrator_next/scripts/metrics" && pwd)/resolve-state-yaml.sh"; do
    if [ -f "$candidate" ]; then
      RESOLVE_SCRIPT="$candidate"
      break
    fi
  done
  if [ -n "$RESOLVE_SCRIPT" ]; then
    STATE_YAML="$(bash "$RESOLVE_SCRIPT" "$ORCHESTRATOR_CHANGE_ID" "$REPO_ROOT" 2>/dev/null)" || STATE_YAML=""
  else
    STATE_YAML=""
  fi
  if [ -n "$STATE_YAML" ]; then
    ARG_DIR="$(dirname "$STATE_YAML")"
  else
    ARG_DIR="$WORKFLOW_DIR"
  fi
else
  ARG_DIR="$WORKFLOW_DIR"
fi

bash "$ESTIMATOR" "$ARG_DIR" > "$TMPOUT" 2> "$TMPERR"
EXIT_CODE=$?

if [ "$EXIT_CODE" -ne 0 ] || [ ! -s "$TMPOUT" ]; then
  reason=$(head -c 200 "$TMPERR" | tr '\n' ' ' | sed 's/"/\\"/g')
  printf '%s\n' "{\"route_preview\": {\"status\": \"estimate_unavailable\", \"reason\": \"$reason\", \"exit_code\": $EXIT_CODE}}"
else
  # estimator prints YAML; encode as a raw string inside the JSON
  python3 - "$TMPOUT" <<'PY'
import json, sys, yaml
with open(sys.argv[1]) as f:
    txt = f.read()
try:
    parsed = yaml.safe_load(txt)
except Exception as exc:
    parsed = {"status": "estimate_unavailable", "reason": f"parse error: {exc}"}
# Unwrap if estimator already nested under route_preview:
if isinstance(parsed, dict) and "route_preview" in parsed:
    print(json.dumps({"route_preview": parsed["route_preview"]}))
else:
    print(json.dumps({"route_preview": parsed if parsed else {"status": "estimate_unavailable", "reason": "empty output"}}))
PY
fi

rm -f "$TMPOUT" "$TMPERR"
