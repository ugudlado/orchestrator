#!/usr/bin/env bash
# load-ticket-context — GET ticket via BACKLOG_URL REST; write ticket-context.md
# under spec/changes/<change_id>/ (same folder as discovery.md / design.md).
# Loop stays ticket-agnostic. Fetch failures abort the workflow (exit 1).
set -euo pipefail

: "${REPO_ROOT:?orchestrator: REPO_ROOT required}"
STATE_YAML="${ORCHESTRATOR_STATE_YAML_PATH:-${STATE_YAML_PATH:?orchestrator: state yaml path required}}"
STATE_DIR="$(dirname "$STATE_YAML")"
LIB="$(cd "$(dirname "$0")/../../lib/ticket" && pwd)/backlog-api.sh"
# shellcheck source=../../lib/ticket/backlog-api.sh
source "$LIB"

_read_state_field() {
  local key="$1"
  grep -E "^${key}:" "$STATE_YAML" 2>/dev/null | head -1 | sed -E 's/^[^:]+:[[:space:]]*//' | tr -d '"'"'" || true
}

ticket_id="$(_read_state_field ticket_id)"
ticketing="$(backlog_api_ticketing)"
change_id="${CHANGE_ID:-${ORCHESTRATOR_CHANGE_ID:-$(_read_state_field change_id)}}"
slug="$(_read_state_field slug)"
if [ -z "$change_id" ] && [ -n "$slug" ]; then
  change_id="$slug"
fi
if [ -z "$ticket_id" ] && [ -n "$change_id" ]; then
  ticket_id="$change_id"
fi
# API ids are prefixed uppercase (ORC-125); seed may store lowercase slug.
if [ -n "$ticket_id" ]; then
  ticket_id="$(printf '%s' "$ticket_id" | tr '[:lower:]' '[:upper:]')"
fi

# Prefer worktree artifact dir (same as discovery.md / design.md); else repo spec/changes.
if [ -n "${ORCHESTRATOR_WORKTREE_ARTIFACT_DIR:-${WORKTREE_ARTIFACT_DIR:-}}" ] && [ -n "$change_id" ]; then
  ARTIFACT_BASE="${ORCHESTRATOR_WORKTREE_ARTIFACT_DIR:-$WORKTREE_ARTIFACT_DIR}"
  OUT_DIR="${ARTIFACT_BASE}/${change_id}"
elif [ -n "$change_id" ]; then
  OUT_DIR="${REPO_ROOT}/spec/changes/${change_id}"
else
  OUT_DIR="${REPO_ROOT}/spec/changes"
fi
mkdir -p "$OUT_DIR"
OUT="${OUT_DIR}/ticket-context.md"
REL_PATH="spec/changes/${change_id:-}/ticket-context.md"

# Write a diagnostic file, log ERROR, emit failed JSON, exit 1 → workflow aborts.
_fail() {
  local msg="$1"
  local detail="${2:-}"
  {
    printf '%s\n' "$msg"
    if [ -n "$detail" ]; then printf 'Detail: %s\n' "$detail"; fi
  } >"$OUT"
  echo "ERROR load-ticket-context: $msg" >&2
  if [ -n "$detail" ]; then echo "ERROR load-ticket-context: $detail" >&2; fi
  printf '%s\n' "{\"status\": \"failed\", \"outputs\": {\"ticket_context\": \"failed\", \"path\": \"${REL_PATH}\"}, \"evidence\": {\"summary\": $(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$msg")}}"
  exit 1
}

if [ "$ticketing" != "backlog" ]; then
  echo "load-ticket-context: ticketing=${ticketing:-unset} — skipping" >&2
  printf '%s\n' '{"status": "completed", "outputs": {"ticket_context": "skipped"}}'
  exit 0
fi

if [ -z "$ticket_id" ]; then
  _fail "[TICKET FETCH FAILED] no ticket_id/change_id in state — do not invent scope from the codebase"
fi

if ! backlog_api_base >/dev/null; then
  _fail "[TICKET FETCH FAILED] BACKLOG_URL/BACKLOG_TOKEN/BACKLOG_PROJECT_ID missing — do not invent scope from the codebase (ticket ${ticket_id})"
fi

err_file="${STATE_DIR}/.load-ticket-context.err"
if ! json="$(backlog_api_get_task "$ticket_id" 2>"$err_file")"; then
  err="$(cat "$err_file" 2>/dev/null || true)"
  rm -f "$err_file"
  _fail "[TICKET FETCH FAILED] GET /api/tasks/${ticket_id} failed — do not invent scope from the codebase" "$err"
fi
rm -f "$err_file"

printf '%s' "$json" | backlog_api_format_plain >"$OUT"
echo "load-ticket-context: wrote ${OUT}" >&2
printf '%s\n' "{\"status\": \"completed\", \"outputs\": {\"ticket_context\": \"ok\", \"path\": \"${REL_PATH}\"}}"
