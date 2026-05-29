#!/usr/bin/env bash
# preview-route.sh — wrap estimate-cost.sh, emit route_preview JSON on last
# stdout line. Non-blocking: unavailability is a normal outcome.
#
# Env inputs:  ORCHESTRATOR_WORKFLOW_DIR (or WORKFLOW_DIR), ORCHESTRATOR_HOME,
#              ORCHESTRATOR_CHANGE_ID, REPO_ROOT
# State lookup (when ORCHESTRATOR_CHANGE_ID is set): delegates to
#   $REPO_ROOT/scripts/resolve-state-yaml.sh — order is live
#   ($WORKFLOW_STATE_DIR/<id>/state.yaml) → main archive → worktree archive.
#   Passes dirname(state.yaml) to the estimator, not the worktree root.
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

if [ -n "${ORCHESTRATOR_CHANGE_ID:-}" ] && [ -n "${REPO_ROOT:-$(git -C "$WORKFLOW_DIR" rev-parse --show-toplevel 2>/dev/null)}" ]; then
  RESOLVE_SCRIPT="$REPO_ROOT/scripts/resolve-state-yaml.sh"
  if [ ! -f "$RESOLVE_SCRIPT" ]; then
    _orch_root="${ORCHESTRATOR_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
    if [ -f "$_orch_root/scripts/resolve-state-yaml.sh" ]; then
      RESOLVE_SCRIPT="$_orch_root/scripts/resolve-state-yaml.sh"
    elif [ -f "$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)/scripts/resolve-state-yaml.sh" ]; then
      RESOLVE_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)/scripts/resolve-state-yaml.sh"
    fi
  fi
  if [ -f "$RESOLVE_SCRIPT" ]; then
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

"$ESTIMATOR" "$ARG_DIR" > "$TMPOUT" 2> "$TMPERR"
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
