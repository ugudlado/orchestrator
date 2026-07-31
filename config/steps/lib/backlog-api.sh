#!/usr/bin/env bash
# Shared Backlog.md REST helpers for workflow step scripts.
# Requires: curl, python3. Auth via BACKLOG_URL + BACKLOG_TOKEN + BACKLOG_PROJECT_ID.
# Sourced by load-ticket-context / ticket-sync / ticket-done — not executed alone.
#
# A PROJECT IS REQUIRED. Backlog's data routes reject a request that names none: a task's
# identity is the pair (project, display id) — display ids are only unique WITHIN a project, so
# "BKG-541" alone is not an address. The server used to fill the missing half from an ambient
# default, which could silently resolve to the WRONG project; it now returns 400 instead.
#
# Project resolution (first non-empty wins) — env-only, no config file read:
#   1. BACKLOG_PROJECT      — env alias (name); takes precedence, see note below.
#   2. BACKLOG_PROJECT_ID   — env (id/guid/name).
# Any value may be an id, guid, or project name. Keeping this env-only (no
# spec/project.yaml fallback) means the CLI's ticketing behavior never
# depends on repo-committed config — only on the environment it's invoked in.

backlog_api_project() {
  # BACKLOG_PROJECT (name) takes precedence: the REST API doesn't resolve the
  # guid until the server ships the guid migration (tasks/project-guid-column).
  printf '%s' "${BACKLOG_PROJECT:-${BACKLOG_PROJECT_ID:-}}"
}

# Which ticketing backend this environment uses (e.g. "backlog") — the engine
# has no notion of ticketing and never writes this into state.yaml; ticket-sync
# steps call this, same as any other workflow-content concern.
backlog_api_ticketing() {
  # Env-driven: the environment that carries the credentials IS the backend
  # selection — no repo-side marker file. Unset env → ticket steps skip.
  if [ -n "${BACKLOG_URL:-}" ] && [ -n "${BACKLOG_TOKEN:-}" ]; then
    printf 'backlog'
  fi
}

backlog_api_base() {
  local base="${BACKLOG_URL:-}"
  base="${base%/}"
  if [ -z "$base" ] || [ -z "${BACKLOG_TOKEN:-}" ] || [ -z "$(backlog_api_project)" ]; then
    return 1
  fi
  printf '%s' "$base"
}

# URL-encode the project ref (names may contain spaces).
_backlog_project_q() {
  python3 -c 'import sys,urllib.parse; print("project=" + urllib.parse.quote(sys.argv[1]))' "$(backlog_api_project)"
}

# GET /api/tasks/:id?project=… → JSON on stdout. Returns curl/http failure as nonzero.
backlog_api_get_task() {
  local ticket_id="$1"
  local base
  base="$(backlog_api_base)" || return 1
  curl -fsS \
    -H "Authorization: Bearer ${BACKLOG_TOKEN}" \
    -H "Accept: application/json" \
    "${base}/api/tasks/${ticket_id}?$(_backlog_project_q)"
}

# PUT /api/tasks/:id?project=… {"status": "..."} — partial update.
backlog_api_put_status() {
  local ticket_id="$1"
  # NOT `status`: that name is read-only in zsh, so sourcing this there would fail confusingly.
  local new_status="$2"
  local base
  base="$(backlog_api_base)" || return 1
  curl -fsS -X PUT \
    -H "Authorization: Bearer ${BACKLOG_TOKEN}" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json" \
    -d "{\"status\":$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$new_status")}" \
    "${base}/api/tasks/${ticket_id}?$(_backlog_project_q)" >/dev/null
}

# The correlation key joining a ticket to the prompt-optimizer ledger rows of
# the same run: results.jsonl rows carry ticket_id/change_id/step_id as fields,
# and this stamps the same triple onto the ticket side. Absent parts are left
# out rather than printed empty, so a key is either usable or clearly missing.
backlog_api_correlation_line() {
  local ticket_id="$1"
  local parts=""
  [ -n "$ticket_id" ] && parts="ticket=${ticket_id}"
  [ -n "${ORCHESTRATOR_CHANGE_ID:-${CHANGE_ID:-}}" ] &&
    parts="${parts:+${parts} }change=${ORCHESTRATOR_CHANGE_ID:-${CHANGE_ID}}"
  [ -n "${ORCHESTRATOR_STEP_ID:-}" ] &&
    parts="${parts:+${parts} }step=${ORCHESTRATOR_STEP_ID}"
  [ -n "$parts" ] && printf 'correlation: %s' "$parts"
  return 0
}

# POST /api/history?project=… {"taskId","body"} — appends a timeline comment
# with the correlation key trailing the given text. Best-effort by contract:
# callers sync ticket status, and a lost comment must not fail that step.
backlog_api_post_comment() {
  local ticket_id="$1"
  local text="$2"
  local base correlation
  base="$(backlog_api_base)" || return 1
  correlation="$(backlog_api_correlation_line "$ticket_id")"
  curl -fsS -X POST \
    -H "Authorization: Bearer ${BACKLOG_TOKEN}" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json" \
    -d "$(python3 -c 'import json,sys; print(json.dumps({"taskId": sys.argv[1], "body": "\n\n".join(a for a in sys.argv[2:] if a)}))' \
      "$ticket_id" "$text" "$correlation")" \
    "${base}/api/history?$(_backlog_project_q)" >/dev/null
}

# JSON task on stdin → plain-text body for agent prompts (title/status/ACs/…).
# Uses python -c so stdin stays available for the JSON pipe (heredoc would steal it).
backlog_api_format_plain() {
  python3 -c '
import json, sys
d = json.load(sys.stdin)
lines = []
title = d.get("title") or ""
tid = d.get("id") or ""
lines.append(f"Task {tid} - {title}" if tid else title)
lines.append("=" * 50)
status = d.get("status")
if status:
    lines.append(f"Status: {status}")
priority = d.get("priority")
if priority:
    lines.append(f"Priority: {priority}")
labels = d.get("labels") or []
if labels:
    lines.append("Labels: " + ", ".join(str(x) for x in labels))
lines.append("")
desc = (d.get("description") or "").strip()
if desc:
    lines.append("Description:")
    lines.append("-" * 50)
    lines.append(desc)
    lines.append("")
acs = d.get("acceptanceCriteriaItems") or []
if acs:
    lines.append("Acceptance Criteria:")
    lines.append("-" * 50)
    for item in acs:
        if not isinstance(item, dict):
            continue
        checked = "x" if item.get("checked") else " "
        idx = item.get("index") or ""
        text = item.get("text") or ""
        prefix = f"#{idx} " if idx != "" else ""
        lines.append(f"- [{checked}] {prefix}{text}".rstrip())
print("\n".join(lines))
'
}
