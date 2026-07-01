#!/usr/bin/env bash
# Shared body for ticket-start/review/qa/rework: backlog task -> $TICKET_SYNC_STATUS.
# ticket-done has extra idempotency/lookup logic and does not use this script.
set -euo pipefail

: "${REPO_ROOT:?orchestrator: REPO_ROOT required}"
: "${TICKET_SYNC_STATUS:?orchestrator: TICKET_SYNC_STATUS required}"
: "${TICKET_SYNC_LOG_PREFIX:?orchestrator: TICKET_SYNC_LOG_PREFIX required}"

STATE_YAML="${ORCHESTRATOR_STATE_YAML_PATH:-${STATE_YAML_PATH:?orchestrator: state yaml path required}}"

_read_state_field() {
  local key="$1"
  grep -E "^${key}:" "$STATE_YAML" 2>/dev/null | head -1 | sed -E 's/^[^:]+:[[:space:]]*//' | tr -d '"'"'" || true
}

ticket_id="$(_read_state_field ticket_id)"
ticketing="$(_read_state_field ticketing)"

if [ -n "$ticket_id" ] && [ "$ticketing" = "backlog" ]; then
  if (cd "$REPO_ROOT" && backlog task edit "$ticket_id" -s "$TICKET_SYNC_STATUS" >/dev/null 2>&1); then
    echo "${TICKET_SYNC_LOG_PREFIX}: ${ticket_id} -> ${TICKET_SYNC_STATUS}" >&2
  else
    echo "WARN ${TICKET_SYNC_LOG_PREFIX}: backlog edit failed for ${ticket_id}" >&2
  fi
fi

printf '%s\n' "{\"status\": \"completed\", \"outputs\": {\"ticket_status_set\": \"${TICKET_SYNC_STATUS}\"}}"
