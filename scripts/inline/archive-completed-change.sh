#!/usr/bin/env bash
# archive-completed-change.sh — copy workflow state to repo archive + commit.
#
# Env inputs:  REPO_ROOT, WORKFLOW_STATE_DIR, CHANGE_ID, ARCHIVE_PATH
#              (ARCHIVE_PATH is relative to REPO_ROOT, e.g. "spec/changes/archive/2026-04-18-hl-287")
# Outputs:     {archive_record: {archived_at, archive_path, commit_sha}}
#   or        {archive_record: {skipped: true, reason}}

set -uo pipefail

REPO_ROOT="${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null)}"
WORKFLOW_STATE_DIR="${WORKFLOW_STATE_DIR:-}"
CHANGE_ID="${CHANGE_ID:-}"
ARCHIVE_PATH="${ARCHIVE_PATH:-}"

if [ -z "$WORKFLOW_STATE_DIR" ] || [ -z "$CHANGE_ID" ] || [ -z "$ARCHIVE_PATH" ]; then
  printf '%s\n' '{"archive_record": {"skipped": true, "reason": "missing required env vars"}}'
  exit 0
fi

SRC="$WORKFLOW_STATE_DIR/$CHANGE_ID"
DST="$REPO_ROOT/$ARCHIVE_PATH"

if [ ! -d "$SRC" ]; then
  printf '%s\n' "{\"archive_record\": {\"skipped\": true, \"reason\": \"source dir missing: $SRC\"}}"
  exit 0
fi

mkdir -p "$(dirname "$DST")"
cp -R "$SRC" "$DST"

ARCHIVED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
cd "$REPO_ROOT"
git add "$ARCHIVE_PATH"
git commit -m "archive: $CHANGE_ID — complete phase artifacts" 2>/dev/null
SHA=$(git rev-parse HEAD 2>/dev/null || echo "")

# After the archive commit succeeds, remove the backlog entry (idempotent).
BACKLOG_DIR="$REPO_ROOT/spec/changes/backlog/$CHANGE_ID"
if [ -d "$BACKLOG_DIR" ]; then
  git -C "$REPO_ROOT" rm -r "$BACKLOG_DIR" >/dev/null 2>&1 || rm -rf "$BACKLOG_DIR"
  git -C "$REPO_ROOT" commit -m "cleanup: remove $CHANGE_ID from backlog" >/dev/null 2>&1 || true
fi

printf '%s\n' "{\"archive_record\": {\"archived_at\": \"$ARCHIVED_AT\", \"archive_path\": \"$ARCHIVE_PATH\", \"commit_sha\": \"$SHA\"}}"
