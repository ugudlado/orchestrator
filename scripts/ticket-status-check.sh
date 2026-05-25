#!/usr/bin/env bash
# ticket-status-check.sh — consult Linear ticket status and map to workflow action
#
# Usage: ticket-status-check.sh <ticket-id> <repo-root>
#
# Reads LINEAR_API_KEY from environment.
# Emits JSON to stdout: {action: init|resume|halt|skip, phase?, checklist?, reason?}
#
# Exit codes:
#   0  Success (always — check JSON .action for the result)
set -euo pipefail

TICKET_ID="${1:-}"
REPO_ROOT="${2:-}"

if [ -z "$TICKET_ID" ] || [ -z "$REPO_ROOT" ]; then
  echo '{"action":"skip","reason":"Missing required arguments: ticket-id and repo-root"}' >&2
  exit 1
fi

# Resolve ticket-status-map.yaml with .orchestrator override support
STATUS_MAP=""
if [ -f "$REPO_ROOT/.orchestrator/config/ticket-status-map.yaml" ]; then
  STATUS_MAP="$REPO_ROOT/.orchestrator/config/ticket-status-map.yaml"
elif [ -f "$REPO_ROOT/config/ticket-status-map.yaml" ]; then
  STATUS_MAP="$REPO_ROOT/config/ticket-status-map.yaml"
elif [ -n "${ORCHESTRATOR_HOME:-}" ] && [ -f "$ORCHESTRATOR_HOME/config/ticket-status-map.yaml" ]; then
  STATUS_MAP="$ORCHESTRATOR_HOME/config/ticket-status-map.yaml"
fi

if [ -z "$STATUS_MAP" ]; then
  echo '{"action":"skip","reason":"config/ticket-status-map.yaml not found"}' >&2
  exit 0
fi

# Check for LINEAR_API_KEY
if [ -z "${LINEAR_API_KEY:-}" ]; then
  echo "WARN: LINEAR_API_KEY not set, skipping ticket status check" >&2
  printf '{"action":"skip","reason":"LINEAR_API_KEY not set"}'
  exit 0
fi

# Lowercase ticket ID for slug matching (e.g., ORC-99 -> orc-99)
TICKET_SLUG=$(echo "$TICKET_ID" | tr '[:upper:]' '[:lower:]')

# Query Linear GraphQL API for ticket status
LINEAR_RESPONSE=$(curl --silent --fail \
  -X POST \
  -H "Authorization: $LINEAR_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"query\":\"{ issue(id: \\\"$TICKET_ID\\\") { state { name } } }\"}" \
  "https://api.linear.app/graphql" 2>/dev/null) || {
  echo "WARN: Linear API request failed for ticket $TICKET_ID" >&2
  printf '{"action":"skip","reason":"Linear API request failed"}'
  exit 0
}

# Extract state name from response
STATE_NAME=$(echo "$LINEAR_RESPONSE" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d['data']['issue']['state']['name'])
except Exception as e:
    sys.exit(1)
" 2>/dev/null) || {
  echo "WARN: Could not parse Linear API response for ticket $TICKET_ID" >&2
  printf '{"action":"skip","reason":"Could not parse Linear API response"}'
  exit 0
}

# Look up action/phase from ticket-status-map.yaml
ACTION=$(yq ".mapping[\"$STATE_NAME\"].action // \"\"" "$STATUS_MAP" 2>/dev/null)
PHASE=$(yq ".mapping[\"$STATE_NAME\"].phase // \"\"" "$STATUS_MAP" 2>/dev/null)
REASON=$(yq ".mapping[\"$STATE_NAME\"].reason // \"\"" "$STATUS_MAP" 2>/dev/null)

# Remove surrounding quotes from yq output (yq v4 emits bare values)
ACTION=$(echo "$ACTION" | tr -d '"')
PHASE=$(echo "$PHASE" | tr -d '"')
REASON=$(echo "$REASON" | tr -d '"')

if [ -z "$ACTION" ]; then
  printf '{"action":"skip","reason":"Unknown Linear status: %s"}' "$STATE_NAME"
  exit 0
fi

# Handle halt action (Done/Cancelled)
if [ "$ACTION" = "halt" ]; then
  printf '{"action":"halt","reason":"%s","ticket_status":"%s"}' "$REASON" "$STATE_NAME"
  exit 0
fi

# Handle init action (Todo/Backlog)
if [ "$ACTION" = "init" ]; then
  printf '{"action":"init","phase":"%s","ticket_id":"%s"}' "$PHASE" "$TICKET_ID"
  exit 0
fi

# Handle resume action (In Progress / In Review)
if [ "$ACTION" = "resume" ]; then
  # Look for matching state.yaml under WORKFLOW_STATE_DIR
  STATE_DIR="${WORKFLOW_STATE_DIR:-$HOME/.workflows}"

  # Search for state.yaml files matching the ticket slug
  FOUND_STATE=""
  if [ -d "$STATE_DIR" ]; then
    # Look for directory names containing the ticket slug (case-insensitive)
    while IFS= read -r -d '' state_file; do
      dir_name=$(basename "$(dirname "$state_file")")
      if echo "$dir_name" | grep -qi "$TICKET_SLUG"; then
        FOUND_STATE="$state_file"
        break
      fi
    done < <(find "$STATE_DIR" -name "state.yaml" -print0 2>/dev/null)
  fi

  if [ -n "$FOUND_STATE" ]; then
    printf '{"action":"resume","phase":"%s","state_yaml":"%s","ticket_id":"%s"}' \
      "$PHASE" "$FOUND_STATE" "$TICKET_ID"
    exit 0
  else
    # Mid-workflow status but no local state — halt with setup checklist
    BRANCH_NAME="feature/$TICKET_SLUG"
    WORKTREE_PATH="$HOME/code/feature_worktrees/$TICKET_SLUG"
    SEED_CMD="orchestrator seed-state --change-id $TICKET_SLUG"
    printf '{"action":"halt","reason":"Ticket status is %s but no local state.yaml found","checklist":["git checkout -b %s","worktree path: %s","run: %s"],"ticket_id":"%s"}' \
      "$STATE_NAME" "$BRANCH_NAME" "$WORKTREE_PATH" "$SEED_CMD" "$TICKET_ID"
    exit 0
  fi
fi

# Fallback
printf '{"action":"skip","reason":"Unhandled action: %s"}' "$ACTION"
exit 0
