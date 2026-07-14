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
# Project resolution precedence (first non-empty wins):
#   1. BACKLOG_PROJECT      — env alias (name); takes precedence, see note below.
#   2. BACKLOG_PROJECT_ID   — env (id/guid/name); the machine-level default.
#   3. spec/project.yaml:project_id under $REPO_ROOT — per-repo workflow config.
# This makes the project a per-repo setting driven by workflow config: a single
# installed engine + shared BACKLOG_URL/BACKLOG_TOKEN drives any repo, and each
# repo names its own backlog project in spec/project.yaml — no global env change
# needed to switch repos. An explicit env var still overrides the config.
# Any value may be an id, guid, or project name.

backlog_api_project() {
  # BACKLOG_PROJECT (name) takes precedence: the REST API doesn't resolve the
  # guid until the server ships the guid migration (tasks/project-guid-column).
  local from_env="${BACKLOG_PROJECT:-${BACKLOG_PROJECT_ID:-}}"
  if [ -n "$from_env" ]; then
    printf '%s' "$from_env"
    return 0
  fi
  # Fall back to the repo's workflow config (spec/project.yaml:project_id).
  local project_yaml="${REPO_ROOT:-}/spec/project.yaml"
  if [ -n "${REPO_ROOT:-}" ] && [ -f "$project_yaml" ]; then
    python3 -c 'import sys, yaml; d = yaml.safe_load(open(sys.argv[1])) or {}; print(d.get("project_id") or "")' "$project_yaml" 2>/dev/null
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
