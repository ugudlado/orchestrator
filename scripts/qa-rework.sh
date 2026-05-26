#!/usr/bin/env bash
# qa-rework.sh — QA failed: move ticket back to In Progress.
#
# Usage: qa-rework.sh <change-id-or-state-yaml> [repo-root]
#
# The branch is untouched. Developer picks it up and resumes work.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/ticket-common.sh
source "$SCRIPT_DIR/lib/ticket-common.sh"

ARG="$(echo "${1:-}" | tr '[:upper:]' '[:lower:]')"
REPO_ROOT="${2:-$(git rev-parse --show-toplevel 2>/dev/null)}"
REPO_ROOT="$(ticket_repo_root "$REPO_ROOT")"

if [ -z "$ARG" ]; then
  echo "Usage: qa-rework.sh <change-id-or-state-yaml> [repo-root]" >&2
  exit 1
fi

# Resolve state.yaml path
STATE_YAML=""
if [ -f "$ARG" ]; then
  STATE_YAML="$(cd "$(dirname "$ARG")" && pwd)/$(basename "$ARG")"
else
  for candidate in \
    "$REPO_ROOT/spec/changes/$ARG/state.yaml" \
    "$REPO_ROOT/spec/changes/archive"/*"-$ARG"/state.yaml; do
    if [ -f "$candidate" ]; then
      STATE_YAML="$candidate"
      break
    fi
  done
fi

if [ -z "$STATE_YAML" ] || [ ! -f "$STATE_YAML" ]; then
  echo "ERROR: cannot locate state.yaml for '$ARG'" >&2
  exit 1
fi

TICKET_ID="$(python3 - "$STATE_YAML" <<'PY'
import sys, yaml, re
with open(sys.argv[1]) as f:
    d = yaml.safe_load(f) or {}
ticket_id = d.get("ticket_id") or ""
if not ticket_id:
    cid = str(d.get("change_id") or "")
    if re.match(r"^[A-Za-z]+-\d+$", cid) or re.match(r"^task-\d+$", cid, re.I):
        ticket_id = cid
print(ticket_id)
PY
)"

if [ -z "$TICKET_ID" ]; then
  echo "ERROR: no ticket_id found in state.yaml" >&2
  exit 1
fi

BACKEND="$(ticket_read_backend "$REPO_ROOT")"
case "$BACKEND" in
  backlog)
    bash "$SCRIPT_DIR/ticket-sync-backlog.sh" "$TICKET_ID" "In Progress" "$REPO_ROOT"
    ;;
  linear)
    bash "$SCRIPT_DIR/ticket-sync-linear.sh" "$TICKET_ID" "In Progress" "$REPO_ROOT"
    ;;
esac

BRANCH="$(python3 - "$STATE_YAML" <<'PY'
import sys, yaml
with open(sys.argv[1]) as f:
    d = yaml.safe_load(f) or {}
print(d.get("branch") or "")
PY
)"

echo "ticket: $TICKET_ID → In Progress"
[ -n "$BRANCH" ] && echo "branch: $BRANCH (retained — resume work there)"
