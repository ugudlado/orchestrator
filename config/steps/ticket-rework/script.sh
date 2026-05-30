#!/usr/bin/env bash
# ticket-rework — QA failed: move ticket back to In Progress (branch unchanged).
#
# Env: ORCHESTRATOR_STATE_YAML_PATH, ORCHESTRATOR_REPO_ROOT
set -uo pipefail

STATE="${ORCHESTRATOR_STATE_YAML_PATH:-}"
REPO_ROOT="${ORCHESTRATOR_REPO_ROOT:-$(pwd)}"

if [ -z "$STATE" ] || [ ! -f "$STATE" ]; then
  echo "ticket-rework: missing ORCHESTRATOR_STATE_YAML_PATH" >&2
  printf '%s\n' '{"status": "failed", "evidence": {"summary": "missing state.yaml"}}'
  exit 1
fi

_STEP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_ORCH_ROOT="${ORCHESTRATOR_HOME:-$(cd "$_STEP_DIR/../../.." && pwd)}"
TICKETS_DIR="$_ORCH_ROOT/orchestrator_next/scripts/tickets"
if [ ! -f "$TICKETS_DIR/ticket-common.sh" ]; then
  TICKETS_DIR="$(cd "$_STEP_DIR/../../../orchestrator_next/scripts/tickets" && pwd)"
fi
# shellcheck source=orchestrator_next/scripts/tickets/ticket-common.sh
source "$TICKETS_DIR/ticket-common.sh"

REPO_ROOT="$(ticket_repo_root "$REPO_ROOT")"

read -r TICKET_ID BRANCH <<EOF
$(python3 - "$STATE" <<'PY'
import sys, yaml, re
with open(sys.argv[1]) as f:
    d = yaml.safe_load(f) or {}
ticket_id = d.get("ticket_id") or ""
if not ticket_id:
    cid = str(d.get("change_id") or "")
    if re.match(r"^[A-Za-z]+-\d+$", cid) or re.match(r"^task-\d+$", cid, re.I):
        ticket_id = cid
print(ticket_id, d.get("branch") or "")
PY
)
EOF

if [ -z "$TICKET_ID" ]; then
  echo "ticket-rework: no ticket_id in state.yaml" >&2
  printf '%s\n' '{"status": "failed", "evidence": {"summary": "no ticket_id"}}'
  exit 1
fi

BACKEND="$(ticket_read_backend "$REPO_ROOT")"
TARGET_STATUS="In Progress"
case "$BACKEND" in
  backlog)
    bash "$TICKETS_DIR/ticket-sync-backlog.sh" "$TICKET_ID" "$TARGET_STATUS" "$REPO_ROOT"
    ;;
  linear)
    bash "$TICKETS_DIR/ticket-sync-linear.sh" "$TICKET_ID" "$TARGET_STATUS" "$REPO_ROOT"
    ;;
  *)
    echo "ticket-rework: unknown ticketing backend" >&2
    printf '%s\n' '{"status": "failed", "evidence": {"summary": "unknown ticketing backend"}}'
    exit 1
    ;;
esac

echo "ticket: $TICKET_ID → $TARGET_STATUS" >&2
[ -n "$BRANCH" ] && echo "branch: $BRANCH (retained — resume work there)" >&2

printf '%s\n' "{\"status\": \"completed\", \"outputs\": {\"ticket_status_set\": \"$TARGET_STATUS\"}}"
