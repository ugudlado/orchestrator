#!/usr/bin/env bash
# ticket-status-check.sh — consult ticket status and map to workflow action
#
# Usage: ticket-status-check.sh <ticket-id> <repo-root>
#
# Fetches status via ticket-fetch-status.sh (backlog CLI or Linear HTTP).
# Emits JSON: {action: init|resume|halt|skip, phase?, checklist?, reason?}
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/ticket-common.sh
source "$SCRIPT_DIR/lib/ticket-common.sh"

TICKET_ID="${1:-}"
REPO_ROOT="${2:-}"

if [ -z "$TICKET_ID" ] || [ -z "$REPO_ROOT" ]; then
  echo '{"action":"skip","reason":"Missing required arguments: ticket-id and repo-root"}' >&2
  exit 1
fi

REPO_ROOT="$(ticket_repo_root "$REPO_ROOT")" || exit 1
TICKETING_BACKEND=$(ticket_read_backend "$REPO_ROOT")

STATUS_MAP=$(ticket_resolve_config "ticket-status-map.yaml" "$REPO_ROOT")
if [ -z "$STATUS_MAP" ] || [ ! -f "$STATUS_MAP" ]; then
  echo '{"action":"skip","reason":"config/ticket-status-map.yaml not found"}' >&2
  exit 0
fi

TICKET_SLUG=$(echo "$TICKET_ID" | tr '[:upper:]' '[:lower:]')

STATE_NAME=""
FETCH_EXIT=0
STATE_NAME=$(bash "$SCRIPT_DIR/ticket-fetch-status.sh" "$TICKET_ID" "$REPO_ROOT" 2>/dev/null) || FETCH_EXIT=$?

if [ "$FETCH_EXIT" -eq 2 ]; then
  echo "WARN: ticketing backend unavailable, skipping ticket status check" >&2
  printf '{"action":"skip","reason":"ticketing backend unavailable"}'
  exit 0
fi
if [ "$FETCH_EXIT" -ne 0 ] || [ -z "$STATE_NAME" ]; then
  echo "WARN: ticket status fetch failed for $TICKET_ID" >&2
  printf '{"action":"skip","reason":"ticket status fetch failed"}'
  exit 0
fi

ACTION=$(yq ".mapping[\"$STATE_NAME\"].action // \"\"" "$STATUS_MAP" 2>/dev/null | tr -d '"')
PHASE=$(yq ".mapping[\"$STATE_NAME\"].phase // \"\"" "$STATUS_MAP" 2>/dev/null | tr -d '"')
REASON=$(yq ".mapping[\"$STATE_NAME\"].reason // \"\"" "$STATUS_MAP" 2>/dev/null | tr -d '"')

if [ -z "$ACTION" ]; then
  printf '{"action":"skip","reason":"Unknown ticket status: %s","ticketing":"%s","ticket_status":"%s"}' \
    "$STATE_NAME" "$TICKETING_BACKEND" "$STATE_NAME"
  exit 0
fi

if [ "$ACTION" = "halt" ]; then
  printf '{"action":"halt","reason":"%s","ticket_status":"%s","ticketing":"%s"}' \
    "$REASON" "$STATE_NAME" "$TICKETING_BACKEND"
  exit 0
fi

if [ "$ACTION" = "init" ]; then
  printf '{"action":"init","phase":"%s","ticket_id":"%s","ticketing":"%s","ticket_status":"%s"}' \
    "$PHASE" "$TICKET_ID" "$TICKETING_BACKEND" "$STATE_NAME"
  exit 0
fi

if [ "$ACTION" = "resume" ]; then
  STATE_DIR="${WORKFLOW_STATE_DIR:-$REPO_ROOT/spec/changes}"
  FOUND_STATE=""

  if [ -d "$STATE_DIR" ]; then
    while IFS= read -r -d '' state_file; do
      dir_name=$(basename "$(dirname "$state_file")")
      if echo "$dir_name" | grep -qi "$TICKET_SLUG"; then
        FOUND_STATE="$state_file"
        break
      fi
    done < <(find "$STATE_DIR" -name "state.yaml" -print0 2>/dev/null)
  fi

  if [ -n "$FOUND_STATE" ]; then
    printf '{"action":"resume","phase":"%s","state_yaml":"%s","ticket_id":"%s","ticketing":"%s","ticket_status":"%s"}' \
      "$PHASE" "$FOUND_STATE" "$TICKET_ID" "$TICKETING_BACKEND" "$STATE_NAME"
    exit 0
  fi

  BRANCH_NAME="feature/$TICKET_SLUG"
  WORKTREE_PATH="$HOME/code/feature_worktrees/$TICKET_SLUG"
  SEED_CMD="orchestrator seed-state --change-id $TICKET_SLUG"
  printf '{"action":"halt","reason":"Ticket status is %s but no local state.yaml found","checklist":["git checkout -b %s","worktree path: %s","run: %s"],"ticket_id":"%s","ticketing":"%s","ticket_status":"%s"}' \
    "$STATE_NAME" "$BRANCH_NAME" "$WORKTREE_PATH" "$SEED_CMD" "$TICKET_ID" "$TICKETING_BACKEND" "$STATE_NAME"
  exit 0
fi

printf '{"action":"skip","reason":"Unhandled action: %s"}' "$ACTION"
exit 0
