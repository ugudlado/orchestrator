#!/usr/bin/env bash
# load-ticket-context — workflow detects ticket vs free text in state.user_input.
# Ticket-shaped → backlog GET (when ticketing=backlog). Else → write brief as
# ticket-context.md. Engine does not classify input; change_id is never used as
# a ticket id fallback.
set -euo pipefail

: "${REPO_ROOT:?orchestrator: REPO_ROOT required}"
STATE_YAML="${ORCHESTRATOR_STATE_YAML_PATH:-${STATE_YAML_PATH:?orchestrator: state yaml path required}}"
STATE_DIR="$(dirname "$STATE_YAML")"
LIB="$(cd "$(dirname "$0")/../../lib/ticket" && pwd)/backlog-api.sh"
# shellcheck source=../../lib/ticket/backlog-api.sh
source "$LIB"

# Read scalar/multiline YAML fields via Python (user_input may be quoted prose).
_read_state_py() {
  local key="$1"
  python3 - "$STATE_YAML" "$key" <<'PY'
import sys, yaml
path, key = sys.argv[1], sys.argv[2]
raw = yaml.safe_load(open(path, encoding="utf-8")) or {}
val = raw.get(key)
if val is None:
    sys.exit(0)
print(val if isinstance(val, str) else str(val), end="")
PY
}

user_input="$(_read_state_py user_input)"
ticket_id="$(_read_state_py ticket_id)"
ticketing="$(backlog_api_ticketing)"
change_id="${CHANGE_ID:-${ORCHESTRATOR_CHANGE_ID:-$(_read_state_py change_id)}}"
slug="$(_read_state_py slug)"
if [ -z "$change_id" ] && [ -n "$slug" ]; then
  change_id="$slug"
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

_fail() {
  local msg="$1"
  local detail="${2:-}"
  {
    printf '%s\n' "$msg"
    if [ -n "$detail" ]; then printf 'Detail: %s\n' "$detail"; fi
  } >"$OUT"
  echo "ERROR load-ticket-context: $msg" >&2
  if [ -n "$detail" ]; then echo "ERROR load-ticket-context: $detail" >&2; fi
  printf '%s\n' "{\"status\": \"failed\", \"outputs\": {\"ticket_context\": \"failed\", \"path\": \"${REL_PATH}\", \"reason\": $(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$msg")}, \"evidence\": {\"summary\": $(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$msg")}}"
  exit 1
}

# Workflow-local ticket detection (not done in the engine).
_is_ticket_id() {
  local s="$1"
  [[ "$s" =~ ^[A-Za-z][A-Za-z0-9]*-[0-9]+$ ]]
}

# Resolve candidate: explicit ticket_id, else user_input if ticket-shaped.
candidate=""
if [ -n "$ticket_id" ] && _is_ticket_id "$ticket_id"; then
  candidate="$ticket_id"
elif [ -n "$user_input" ] && _is_ticket_id "$(printf '%s' "$user_input" | tr -d '[:space:]')"; then
  candidate="$(printf '%s' "$user_input" | tr -d '[:space:]')"
fi

if [ -n "$candidate" ]; then
  candidate="$(printf '%s' "$candidate" | tr '[:lower:]' '[:upper:]')"
  if [ "$ticketing" != "backlog" ]; then
    {
      printf '# Ticket\n\n'
      printf '**Id:** %s\n\n' "$candidate"
      printf 'Ticketing provider unset (ticketing=%s) — no remote body fetched.\n' "${ticketing:-unset}"
    } >"$OUT"
    echo "load-ticket-context: ticket ${candidate} — ticketing skipped, stub written" >&2
    printf '%s\n' "{\"status\": \"completed\", \"outputs\": {\"ticket_context\": \"stub\", \"path\": \"${REL_PATH}\", \"reason\": \"ticketing unset; stub for ${candidate}\"}, \"state_patch\": {\"ticket_id\": \"${candidate}\"}}"
    exit 0
  fi
  if ! backlog_api_base >/dev/null; then
    _fail "[TICKET FETCH FAILED] BACKLOG_URL/BACKLOG_TOKEN/BACKLOG_PROJECT_ID missing — do not invent scope from the codebase (ticket ${candidate})"
  fi
  err_file="${STATE_DIR}/.load-ticket-context.err"
  if ! json="$(backlog_api_get_task "$candidate" 2>"$err_file")"; then
    err="$(cat "$err_file" 2>/dev/null || true)"
    rm -f "$err_file"
    _fail "[TICKET FETCH FAILED] GET tasks/${candidate} failed — do not invent scope from the codebase" "$err"
  fi
  rm -f "$err_file"
  printf '%s' "$json" | backlog_api_format_plain >"$OUT"
  echo "load-ticket-context: wrote ${OUT} (ticket ${candidate})" >&2
  printf '%s\n' "{\"status\": \"completed\", \"outputs\": {\"ticket_context\": \"ok\", \"path\": \"${REL_PATH}\", \"reason\": \"fetched ${candidate}\"}, \"state_patch\": {\"ticket_id\": \"${candidate}\"}}"
  exit 0
fi

# Free text brief (or empty)
if [ -z "$user_input" ]; then
  if [ "$ticketing" != "backlog" ]; then
    echo "load-ticket-context: no user_input — skipping" >&2
    printf '%s\n' '{"status": "completed", "outputs": {"ticket_context": "skipped", "reason": "no user_input; ticketing unset"}}'
    exit 0
  fi
  _fail "[TICKET FETCH FAILED] no user_input in state — pass a ticket id or brief text when starting the workflow"
fi

{
  printf '# Feature brief\n\n'
  printf '%s\n' "$user_input"
} >"$OUT"
echo "load-ticket-context: wrote brief ${OUT}" >&2
# Escape user_input for JSON reason via python
reason_json="$(python3 -c 'import json,sys; print(json.dumps("brief from user_input"))')"
printf '%s\n' "{\"status\": \"completed\", \"outputs\": {\"ticket_context\": \"from_text\", \"path\": \"${REL_PATH}\", \"reason\": ${reason_json}}}"
