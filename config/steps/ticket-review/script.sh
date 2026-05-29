#!/usr/bin/env bash
# ticket-review — move the ticket to the code-review status.
# Replaces the ticket-step-sync.yaml outbound hook after run-phase-review.
# Non-blocking: a ticketing failure never fails the workflow.
#
# Env: ORCHESTRATOR_STATE_YAML_PATH, ORCHESTRATOR_REPO_ROOT
set -uo pipefail

BACKLOG_STATUS="Code Review"
LINEAR_STATUS="In Review"

STATE="${ORCHESTRATOR_STATE_YAML_PATH:-}"
REPO_ROOT="${ORCHESTRATOR_REPO_ROOT:-$(pwd)}"

read -r TICKET_ID TICKETING <<EOF
$(python3 - "$STATE" <<'PY'
import sys, yaml
try:
    with open(sys.argv[1]) as f:
        d = yaml.safe_load(f) or {}
except Exception:
    d = {}
print(d.get("ticket_id", ""), d.get("ticketing", ""))
PY
)
EOF

if [ -n "$TICKET_ID" ] && [ "$TICKETING" = "backlog" ] && command -v backlog >/dev/null 2>&1; then
  ( cd "$REPO_ROOT" && backlog task edit "$TICKET_ID" -s "$BACKLOG_STATUS" >/dev/null 2>&1 ) \
    && echo "ticket-review: $TICKET_ID -> $BACKLOG_STATUS" >&2 \
    || echo "WARN ticket-review: backlog edit failed for $TICKET_ID" >&2
fi

printf '%s\n' '{"status": "completed", "outputs": {"ticket_status_set": "'"$BACKLOG_STATUS"'"}}'
