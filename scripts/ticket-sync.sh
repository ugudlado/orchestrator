#!/usr/bin/env bash
# ticket-sync.sh — push workflow step completion to ticketing backend (router)
#
# Usage: ticket-sync.sh <state.yaml> <completed_step_id>
#
# Delegates to ticket-sync-backlog.sh or ticket-sync-linear.sh after resolving
# target status from config/ticket-step-sync.yaml.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/ticket-common.sh
source "$SCRIPT_DIR/lib/ticket-common.sh"

STATE_YAML="${1:-}"
COMPLETED_STEP="${2:-}"

if [ -z "$STATE_YAML" ] || [ -z "$COMPLETED_STEP" ]; then
  echo "Usage: ticket-sync.sh <state.yaml> <completed_step_id>" >&2
  exit 1
fi

if [ ! -f "$STATE_YAML" ]; then
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

SYNC_YAML=$(ticket_resolve_config "ticket-step-sync.yaml" "$REPO_ROOT")
if [ -z "$SYNC_YAML" ] || [ ! -f "$SYNC_YAML" ]; then
  exit 0
fi

SYNC_PLAN=$(python3 - "$STATE_YAML" "$COMPLETED_STEP" "$SYNC_YAML" "$REPO_ROOT/spec/project.yaml" <<'PY'
import fnmatch
import json
import re
import sys
from pathlib import Path
import yaml

state_path, step_id, sync_path, project_path = sys.argv[1:5]

with open(state_path) as f:
    state = yaml.safe_load(f) or {}

ticketing = "backlog"
if Path(project_path).is_file():
    with open(project_path) as f:
        project = yaml.safe_load(f) or {}
    ticketing = str(project.get("ticketing") or "backlog").strip().lower()

with open(sync_path) as f:
    sync_cfg = yaml.safe_load(f) or {}

mapping = sync_cfg.get("on_step_complete") or {}
target = None
for key, val in mapping.items():
    if not isinstance(val, dict):
        continue
    if key.startswith("pattern:"):
        if fnmatch.fnmatch(step_id, key[len("pattern:"):]):
            target = val
            break
    elif key == step_id:
        target = val
        break

if not target:
    sys.exit(0)

status = target.get(ticketing) or target.get("backlog" if ticketing == "backlog" else "linear")
if not status:
    sys.exit(0)

ticket_id = state.get("ticket_id") or ""
if not ticket_id:
    cid = str(state.get("change_id") or "")
    if re.match(r"^[A-Za-z]+-\d+$", cid) or re.match(r"^task-\d+$", cid, re.I):
        ticket_id = cid

if not ticket_id:
    sys.exit(0)

print(json.dumps({
    "ticketing": ticketing,
    "ticket_id": str(ticket_id),
    "status": str(status),
    "step_id": step_id,
}))
PY
) || exit 0

if [ -z "$SYNC_PLAN" ]; then
  exit 0
fi

TICKETING=$(echo "$SYNC_PLAN" | python3 -c "import sys,json; print(json.load(sys.stdin)['ticketing'])")
TICKET_ID=$(echo "$SYNC_PLAN" | python3 -c "import sys,json; print(json.load(sys.stdin)['ticket_id'])")
TARGET_STATUS=$(echo "$SYNC_PLAN" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")

case "$TICKETING" in
  backlog)
    bash "$SCRIPT_DIR/ticket-sync-backlog.sh" "$TICKET_ID" "$TARGET_STATUS" "$REPO_ROOT" || true
    ;;
  linear)
    bash "$SCRIPT_DIR/ticket-sync-linear.sh" "$TICKET_ID" "$TARGET_STATUS" "$REPO_ROOT" || true
    ;;
esac

# Mirror outbound status on state.yaml (no rework flag — workflow drove this update)
echo "$SYNC_PLAN" | python3 -c "
import json, sys
p = json.load(sys.stdin)
print(json.dumps({
    'ticket_id': p['ticket_id'],
    'ticket_status': p['status'],
    'ticketing': p['ticketing'],
    'ticket_rework': False,
}))
" | bash "$SCRIPT_DIR/ticket-state-update.sh" "$STATE_YAML" 2>/dev/null || true

exit 0
