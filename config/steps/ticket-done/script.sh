#!/usr/bin/env bash
# ticket-done — backlog task -> Done (params from contract.yaml).
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
change_id="${CHANGE_ID:-$(_read_state_field change_id)}"
synced=""

if [ -n "$ticket_id" ] && [ "$ticketing" = "backlog" ]; then
  if (cd "$REPO_ROOT" && backlog task edit "$ticket_id" -s "$TICKET_SYNC_STATUS" >/dev/null 2>&1); then
    echo "${TICKET_SYNC_LOG_PREFIX}: ${ticket_id} -> ${TICKET_SYNC_STATUS}" >&2
    synced="$ticket_id"
  else
    echo "WARN ${TICKET_SYNC_LOG_PREFIX}: backlog edit failed for ${ticket_id}" >&2
  fi
elif [ -n "$change_id" ] && command -v backlog >/dev/null 2>&1 \
     && [ -d "$REPO_ROOT/spec/changes/backlog" ]; then
  ticket_id=$(backlog task list --plain 2>/dev/null \
    | grep -oE "ORC-[0-9]+" \
    | while read -r tid; do
        if backlog task "$tid" --plain 2>/dev/null \
           | grep -qE "^Labels:.*\\bslug-${change_id}\\b"; then
          echo "$tid"
          break
        fi
      done | head -1)
  if [ -n "$ticket_id" ] \
     && (cd "$REPO_ROOT" && backlog task edit "$ticket_id" -s "$TICKET_SYNC_STATUS" >/dev/null 2>&1); then
    echo "${TICKET_SYNC_LOG_PREFIX}: ${ticket_id} -> ${TICKET_SYNC_STATUS} (slug-${change_id})" >&2
    synced="$ticket_id"
  fi
fi

printf '%s\n' "{\"status\": \"completed\", \"outputs\": {\"ticket_status_set\": \"${TICKET_SYNC_STATUS}\", \"ticket_id\": \"${synced}\"}}"
