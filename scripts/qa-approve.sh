#!/usr/bin/env bash
# qa-approve.sh — QA passed: merge branch to main, move ticket to Done, delete branch.
#
# Usage: qa-approve.sh <change-id-or-state-yaml> [repo-root]
#
# Examples:
#   qa-approve.sh orc-86
#   qa-approve.sh spec/changes/archive/2026-05-26-orc-86/state.yaml
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/ticket-common.sh
source "$SCRIPT_DIR/lib/ticket-common.sh"

ARG="$(echo "${1:-}" | tr '[:upper:]' '[:lower:]')"
REPO_ROOT="${2:-$(git rev-parse --show-toplevel 2>/dev/null)}"
REPO_ROOT="$(ticket_repo_root "$REPO_ROOT")"

if [ -z "$ARG" ]; then
  echo "Usage: qa-approve.sh <change-id-or-state-yaml> [repo-root]" >&2
  exit 1
fi

# Resolve state.yaml path
STATE_YAML=""
if [ -f "$ARG" ]; then
  STATE_YAML="$(cd "$(dirname "$ARG")" && pwd)/$(basename "$ARG")"
else
  for candidate in \
    "$REPO_ROOT/spec/changes/$ARG/state.yaml" \
    "$REPO_ROOT/spec/changes/archive/$ARG/state.yaml" \
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

# Read ticket_id and branch from state.yaml
read -r TICKET_ID BRANCH <<< "$(python3 - "$STATE_YAML" <<'PY'
import sys, yaml, re
with open(sys.argv[1]) as f:
    d = yaml.safe_load(f) or {}
ticket_id = d.get("ticket_id") or ""
if not ticket_id:
    cid = str(d.get("change_id") or "")
    if re.match(r"^[A-Za-z]+-\d+$", cid) or re.match(r"^task-\d+$", cid, re.I):
        ticket_id = cid
branch = d.get("branch") or ""
print(ticket_id, branch)
PY
)"

# --- Step 1: merge branch to main -------------------------------------------
INLINE_DIR="${ORCHESTRATOR_HOME:-$REPO_ROOT}/config/scripts/inline"
MERGE_RESULT="$(STATE_YAML_PATH="$STATE_YAML" REPO_ROOT="$REPO_ROOT" \
  bash "$INLINE_DIR/merge-to-main.sh" 2>&1)"
MERGE_OK="$(printf '%s\n' "$MERGE_RESULT" | python3 -c "
import sys, json
for line in sys.stdin:
    line = line.strip()
    if line.startswith('{') and line.endswith('}'):
        try:
            obj = json.loads(line)
            r = obj.get('merge_record', obj)
            merged = r.get('merged', False)
            reason = r.get('reason', '')
            branch = r.get('branch', '')
            sha = r.get('merge_sha', '')
            print('merged' if merged else 'skipped', reason, branch, sha)
        except Exception:
            pass
" 2>/dev/null || echo "error")"

STATUS="${MERGE_OK%% *}"
case "$STATUS" in
  merged)
    MERGE_SHA="${MERGE_OK##* }"
    MERGE_BRANCH="$(echo "$MERGE_OK" | awk '{print $3}')"
    echo "merge: $MERGE_BRANCH → main ($MERGE_SHA)"
    ;;
  skipped)
    echo "merge: skipped (${MERGE_OK#skipped })"
    ;;
  *)
    echo "ERROR: merge failed — aborting" >&2
    printf '%s\n' "$MERGE_RESULT" >&2
    exit 1
    ;;
esac

# --- Step 2: move ticket to Done --------------------------------------------
if [ -n "$TICKET_ID" ]; then
  BACKEND="$(ticket_read_backend "$REPO_ROOT")"
  case "$BACKEND" in
    backlog)
      bash "$SCRIPT_DIR/ticket-sync-backlog.sh" "$TICKET_ID" "Done" "$REPO_ROOT" || true
      ;;
    linear)
      bash "$SCRIPT_DIR/ticket-sync-linear.sh" "$TICKET_ID" "Done" "$REPO_ROOT" || true
      ;;
  esac
  echo "ticket: $TICKET_ID → Done"
else
  echo "WARN: no ticket_id in state.yaml — skipping ticket update" >&2
fi

# --- Step 3: delete the feature branch --------------------------------------
if [ -n "$BRANCH" ]; then
  if git -C "$REPO_ROOT" branch -D "$BRANCH" 2>/dev/null; then
    echo "branch: $BRANCH deleted"
  else
    echo "WARN: branch $BRANCH not found or already deleted" >&2
  fi
else
  echo "WARN: no branch in state.yaml — skipping branch deletion" >&2
fi
