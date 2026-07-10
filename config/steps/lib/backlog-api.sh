#!/usr/bin/env bash
# Shared Backlog.md REST helpers for workflow step scripts.
# Requires: curl, python3. Auth via BACKLOG_URL + BACKLOG_TOKEN.
# Sourced by load-ticket-context / ticket-sync / ticket-done — not executed alone.

backlog_api_base() {
  local base="${BACKLOG_URL:-}"
  base="${base%/}"
  if [ -z "$base" ] || [ -z "${BACKLOG_TOKEN:-}" ]; then
    return 1
  fi
  printf '%s' "$base"
}

# GET /api/tasks/:id → JSON on stdout. Returns curl/http failure as nonzero.
backlog_api_get_task() {
  local ticket_id="$1"
  local base
  base="$(backlog_api_base)" || return 1
  curl -fsS \
    -H "Authorization: Bearer ${BACKLOG_TOKEN}" \
    -H "Accept: application/json" \
    "${base}/api/tasks/${ticket_id}"
}

# PUT /api/tasks/:id {"status": "..."} — partial update.
backlog_api_put_status() {
  local ticket_id="$1"
  local status="$2"
  local base
  base="$(backlog_api_base)" || return 1
  curl -fsS -X PUT \
    -H "Authorization: Bearer ${BACKLOG_TOKEN}" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json" \
    -d "{\"status\":$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$status")}" \
    "${base}/api/tasks/${ticket_id}" >/dev/null
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
