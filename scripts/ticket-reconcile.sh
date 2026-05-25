#!/usr/bin/env bash
# ticket-reconcile.sh — poll ticket status, update state.yaml, detect review rework
#
# Usage: ticket-reconcile.sh <state.yaml>
#
# Called at the start of each run-workflow.sh loop iteration (after a step completes).
# When ticket moves from Code Review / In Review back to In Progress, sets
# ticket_rework and flags.rework_from_review so the developer loop can continue.
#
# Emits JSON summary to stdout.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/ticket-common.sh
source "$SCRIPT_DIR/lib/ticket-common.sh"

STATE_YAML="${1:-}"

if [ -z "$STATE_YAML" ] || [ ! -f "$STATE_YAML" ]; then
  printf '{"action":"skip","reason":"state.yaml missing"}'
  exit 0
fi

STATE_YAML=$(cd "$(dirname "$STATE_YAML")" && pwd)/$(basename "$STATE_YAML")

REPO_ROOT=$(python3 -c "
import yaml
with open('$STATE_YAML') as f:
    d = yaml.safe_load(f) or {}
print(d.get('repo_root') or '')
" 2>/dev/null || echo "")

if [ -z "$REPO_ROOT" ] || [ ! -d "$REPO_ROOT" ]; then
  REPO_ROOT="$(cd "$(dirname "$STATE_YAML")/../.." && pwd)"
fi
REPO_ROOT="$(ticket_repo_root "$REPO_ROOT")"

TICKET_ID=$(python3 -c "
import re, yaml
with open('$STATE_YAML') as f:
    d = yaml.safe_load(f) or {}
tid = d.get('ticket_id') or ''
if not tid:
    cid = str(d.get('change_id') or '')
    if re.match(r'^[A-Za-z]+-\d+$', cid) or re.match(r'^task-\d+$', cid, re.I):
        tid = cid
print(tid)
" 2>/dev/null || echo "")

if [ -z "$TICKET_ID" ]; then
  printf '{"action":"skip","reason":"no ticket_id on state"}'
  exit 0
fi

BACKEND=$(ticket_read_backend "$REPO_ROOT")
FETCH="$SCRIPT_DIR/ticket-fetch-status.sh"

CURRENT_STATUS=""
FETCH_EXIT=0
CURRENT_STATUS=$(bash "$FETCH" "$TICKET_ID" "$REPO_ROOT" 2>/dev/null) || FETCH_EXIT=$?

if [ "$FETCH_EXIT" -eq 2 ]; then
  printf '{"action":"skip","reason":"ticketing backend unavailable"}'
  exit 0
fi
if [ "$FETCH_EXIT" -ne 0 ] || [ -z "$CURRENT_STATUS" ]; then
  printf '{"action":"skip","reason":"ticket status fetch failed"}'
  exit 0
fi

RESULT=$(python3 - "$STATE_YAML" "$CURRENT_STATUS" "$BACKEND" "$TICKET_ID" <<'PY'
import json, sys
import yaml

path, current, backend, ticket_id = sys.argv[1:5]
REVIEW_STATUSES = {"In Review", "Code Review", "in review", "code review"}

with open(path) as f:
    state = yaml.safe_load(f) or {}

previous = str(state.get("ticket_status") or "")
rework = False
if current == "In Progress" and previous in REVIEW_STATUSES:
    rework = True

patch = {
    "ticket_id": ticket_id,
    "ticket_status": current,
    "ticketing": backend,
    "ticket_rework": rework,
}
if rework:
    patch["flags"] = {"rework_from_review": True}

print(json.dumps({
    "action": "rework" if rework else "updated",
    "ticket_status": current,
    "previous_ticket_status": previous or None,
    "ticket_rework": rework,
    "patch": patch,
}))
PY
)

echo "$RESULT" | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin)['patch']))" \
  | bash "$SCRIPT_DIR/ticket-state-update.sh" "$STATE_YAML"

echo "$RESULT"
