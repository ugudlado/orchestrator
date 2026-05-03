#!/usr/bin/env bash
# archive-completed-change.sh — move workflow state to repo archive + commit.
#
# Env inputs:  REPO_ROOT, CHANGE_ID, ARCHIVE_PATH
#              (ARCHIVE_PATH is relative to REPO_ROOT, e.g. "spec/changes/archive/2026-04-18-hl-287")
#              WORKFLOW_STATE_DIR is accepted for interface compatibility but not used to
#              locate the source — source is always $REPO_ROOT/spec/changes/$CHANGE_ID.
# Outputs:     {archive_record: {archived_at, archive_path, commit_sha}}
#   or        {archive_record: {skipped: true, reason}}

set -uo pipefail

REPO_ROOT="${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null)}"
CHANGE_ID="${CHANGE_ID:-}"
ARCHIVE_PATH="${ARCHIVE_PATH:-}"

if [ -z "$CHANGE_ID" ] || [ -z "$ARCHIVE_PATH" ]; then
  printf '%s\n' '{"archive_record": {"skipped": true, "reason": "missing required env vars"}}'
  exit 0
fi

# Source is always spec/changes/<slug>/ — the single canonical location for all
# artifacts (state.yaml, plan.yaml, spec.md, design.md, tasks.md, etc.).
SRC="$REPO_ROOT/spec/changes/$CHANGE_ID"
DST="$REPO_ROOT/$ARCHIVE_PATH"

if [ ! -d "$SRC" ]; then
  printf '%s\n' "{\"archive_record\": {\"skipped\": true, \"reason\": \"source dir missing: $SRC\"}}"
  exit 0
fi

mkdir -p "$(dirname "$DST")"
mv "$SRC" "$DST"

# Defensive cleanup: remove legacy .state/<slug>/ if it still exists from a
# pre-consolidation run (ORC-36 one-time migration or CI override).
LEGACY_STATE_DIR="${REPO_ROOT}/.state/${CHANGE_ID}"
if [ -d "$LEGACY_STATE_DIR" ]; then
  rm -rf "$LEGACY_STATE_DIR"
fi

ARCHIVED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
cd "$REPO_ROOT"
git add "$ARCHIVE_PATH"
git commit -m "archive: $CHANGE_ID — complete phase artifacts" 2>/dev/null
SHA=$(git rev-parse HEAD 2>/dev/null || echo "")

# After the archive commit succeeds, mark the matching backlog task as Done
# (idempotent). Backlog lives in spec/changes/backlog/ managed by the
# `backlog` CLI; tasks carry a `slug-<change_id>` label set at migration time.
# Look up the task id by label match, then transition status. If no match,
# skip silently — the change may have predated the backlog migration or
# never had a backlog entry.
if command -v backlog >/dev/null 2>&1 && [ -d "$REPO_ROOT/spec/changes/backlog" ]; then
  TASK_ID=$(backlog task list --plain 2>/dev/null \
            | grep -oE "ORC-[0-9]+" \
            | while read -r tid; do
                if backlog task "$tid" --plain 2>/dev/null \
                   | grep -qE "^Labels:.*\bslug-${CHANGE_ID}\b"; then
                  echo "$tid"; break
                fi
              done | head -1)
  if [ -n "$TASK_ID" ]; then
    backlog task edit "$TASK_ID" -s Done >/dev/null 2>&1 || true
    git -C "$REPO_ROOT" add "spec/changes/backlog/tasks/" >/dev/null 2>&1 || true
    git -C "$REPO_ROOT" commit -m "cleanup: mark $TASK_ID ($CHANGE_ID) Done" >/dev/null 2>&1 || true
  fi
fi

printf '%s\n' "{\"archive_record\": {\"archived_at\": \"$ARCHIVED_AT\", \"archive_path\": \"$ARCHIVE_PATH\", \"commit_sha\": \"$SHA\"}}"
