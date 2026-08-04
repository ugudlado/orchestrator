#!/usr/bin/env bash
# Shared body for ticket-start/review/qa/rework: backlog task -> $TICKET_SYNC_STATUS via REST.
# ticket-done has extra idempotency logic and does not use this script.
# When ticketing=backlog, REST failure aborts the workflow (exit 1).
set -euo pipefail

: "${REPO_ROOT:?orchestrator: REPO_ROOT required}"
: "${TICKET_SYNC_STATUS:?orchestrator: TICKET_SYNC_STATUS required}"
: "${TICKET_SYNC_LOG_PREFIX:?orchestrator: TICKET_SYNC_LOG_PREFIX required}"

STATE_YAML="${ORCHESTRATOR_STATE_YAML_PATH:-${STATE_YAML_PATH:?orchestrator: state yaml path required}}"
LIB="$(cd "$(dirname "$0")" && pwd)/backlog-api.sh"
# shellcheck source=backlog-api.sh
source "$LIB"

_read_state_field() {
  local key="$1"
  grep -E "^${key}:" "$STATE_YAML" 2>/dev/null | head -1 | sed -E 's/^[^:]+:[[:space:]]*//' | tr -d '"'"'" || true
}

ticket_id="$(_read_state_field ticket_id)"
ticketing="$(backlog_api_ticketing)"
if [ -n "$ticket_id" ]; then
  ticket_id="$(printf '%s' "$ticket_id" | tr '[:lower:]' '[:upper:]')"
fi

if [ -z "$ticket_id" ] || [ "$ticketing" != "backlog" ]; then
  printf '%s\n' "{\"status\": \"completed\", \"outputs\": {\"ticket_status_set\": \"${TICKET_SYNC_STATUS}\"}}"
  exit 0
fi

if ! backlog_api_base >/dev/null; then
  echo "ERROR ${TICKET_SYNC_LOG_PREFIX}: BACKLOG_URL/BACKLOG_TOKEN/BACKLOG_PROJECT_ID missing for ${ticket_id}" >&2
  printf '%s\n' "{\"status\": \"failed\", \"outputs\": {}, \"evidence\": {\"summary\": \"BACKLOG_URL/BACKLOG_TOKEN/BACKLOG_PROJECT_ID missing\"}}"
  exit 1
fi

if ! backlog_api_put_status "$ticket_id" "$TICKET_SYNC_STATUS"; then
  echo "ERROR ${TICKET_SYNC_LOG_PREFIX}: REST status update failed for ${ticket_id} -> ${TICKET_SYNC_STATUS}" >&2
  printf '%s\n' "{\"status\": \"failed\", \"outputs\": {}, \"evidence\": {\"summary\": \"PUT /api/tasks/${ticket_id} status failed\"}}"
  exit 1
fi

echo "${TICKET_SYNC_LOG_PREFIX}: ${ticket_id} -> ${TICKET_SYNC_STATUS}" >&2
backlog_api_post_comment "$ticket_id" "${TICKET_SYNC_LOG_PREFIX}: status set to ${TICKET_SYNC_STATUS}." ||
  echo "WARN ${TICKET_SYNC_LOG_PREFIX}: comment post failed for ${ticket_id}" >&2
printf '%s\n' "{\"status\": \"completed\", \"outputs\": {\"ticket_status_set\": \"${TICKET_SYNC_STATUS}\"}}"
