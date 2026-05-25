#!/usr/bin/env bash
# preview-route.sh — wrap estimate-cost.sh, emit route_preview JSON on last
# stdout line. Non-blocking: unavailability is a normal outcome.
#
# Env inputs:  ORCHESTRATOR_WORKFLOW_DIR (or WORKFLOW_DIR), ORCHESTRATOR_HOME
# Outputs:     {route_preview: {...}} or {route_preview: {status: "estimate_unavailable", reason: "..."}}

set -uo pipefail

WORKFLOW_DIR="${ORCHESTRATOR_WORKFLOW_DIR:-${WORKFLOW_DIR:-}}"
ORCHESTRATOR_HOME="${ORCHESTRATOR_HOME:-$HOME/.config/orchestrator}"
ESTIMATOR="$ORCHESTRATOR_HOME/config/scripts/estimate-cost.sh"

if [ ! -x "$ESTIMATOR" ]; then
  printf '%s\n' "{\"route_preview\": {\"status\": \"estimate_unavailable\", \"reason\": \"estimator not found at $ESTIMATOR\"}}"
  exit 0
fi

if [ -z "$WORKFLOW_DIR" ] || [ ! -d "$WORKFLOW_DIR" ]; then
  printf '%s\n' "{\"route_preview\": {\"status\": \"estimate_unavailable\", \"reason\": \"workflow dir missing\"}}"
  exit 0
fi

TMPOUT=$(mktemp "${TMPDIR:-/tmp}/preview-route-out.XXXXXX")
TMPERR=$(mktemp "${TMPDIR:-/tmp}/preview-route-err.XXXXXX")
"$ESTIMATOR" "$WORKFLOW_DIR" > "$TMPOUT" 2> "$TMPERR"
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
