#!/usr/bin/env bash
# ticket-done — backlog task -> Done via REST (params from contract.yaml).
# When ticketing=backlog, REST failure aborts the workflow (exit 1).
set -euo pipefail

: "${REPO_ROOT:?orchestrator: REPO_ROOT required}"
: "${TICKET_SYNC_STATUS:?orchestrator: TICKET_SYNC_STATUS required}"
: "${TICKET_SYNC_LOG_PREFIX:?orchestrator: TICKET_SYNC_LOG_PREFIX required}"

STATE_YAML="${ORCHESTRATOR_STATE_YAML_PATH:-${STATE_YAML_PATH:?orchestrator: state yaml path required}}"
LIB="$(cd "$(dirname "$0")/../lib" && pwd)/backlog-api.sh"
# shellcheck source=../lib/backlog-api.sh
source "$LIB"

_read_state_field() {
  local key="$1"
  grep -E "^${key}:" "$STATE_YAML" 2>/dev/null | head -1 | sed -E 's/^[^:]+:[[:space:]]*//' | tr -d '"'"'" || true
}

ticket_id="$(_read_state_field ticket_id)"
ticketing="$(backlog_api_ticketing)"
synced=""
if [ -n "$ticket_id" ]; then
  ticket_id="$(printf '%s' "$ticket_id" | tr '[:lower:]' '[:upper:]')"
fi

if [ -z "$ticket_id" ] || [ "$ticketing" != "backlog" ]; then
  printf '%s\n' "{\"status\": \"completed\", \"outputs\": {\"ticket_status_set\": \"${TICKET_SYNC_STATUS}\", \"ticket_id\": \"\"}}"
  exit 0
fi

if ! backlog_api_base >/dev/null; then
  echo "ERROR ${TICKET_SYNC_LOG_PREFIX}: BACKLOG_URL/BACKLOG_TOKEN/BACKLOG_PROJECT_ID missing for ${ticket_id}" >&2
  printf '%s\n' "{\"status\": \"failed\", \"outputs\": {}, \"evidence\": {\"summary\": \"BACKLOG_URL/BACKLOG_TOKEN/BACKLOG_PROJECT_ID missing\"}}"
  exit 1
fi

_current_status=""
if json="$(backlog_api_get_task "$ticket_id" 2>/dev/null)"; then
  _current_status="$(printf '%s' "$json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status") or "")')"
fi
if [ "$_current_status" = "$TICKET_SYNC_STATUS" ]; then
  echo "${TICKET_SYNC_LOG_PREFIX}: ${ticket_id} already ${TICKET_SYNC_STATUS} — skipping" >&2
  synced="$ticket_id"
elif backlog_api_put_status "$ticket_id" "$TICKET_SYNC_STATUS"; then
  echo "${TICKET_SYNC_LOG_PREFIX}: ${ticket_id} -> ${TICKET_SYNC_STATUS}" >&2
  synced="$ticket_id"
  # Only on a real transition: the skip branch above must not append a
  # duplicate correlation comment when the step is re-run.
  backlog_api_post_comment "$ticket_id" "${TICKET_SYNC_LOG_PREFIX}: status set to ${TICKET_SYNC_STATUS}." ||
    echo "WARN ${TICKET_SYNC_LOG_PREFIX}: comment post failed for ${ticket_id}" >&2
else
  echo "ERROR ${TICKET_SYNC_LOG_PREFIX}: REST status update failed for ${ticket_id}" >&2
  printf '%s\n' "{\"status\": \"failed\", \"outputs\": {}, \"evidence\": {\"summary\": \"PUT /api/tasks/${ticket_id} status failed\"}}"
  exit 1
fi

printf '%s\n' "{\"status\": \"completed\", \"outputs\": {\"ticket_status_set\": \"${TICKET_SYNC_STATUS}\", \"ticket_id\": \"${synced}\"}}"
