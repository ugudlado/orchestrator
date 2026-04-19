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
# Backlog lives in a single file (spec/changes/backlog.md) with one H2 per
# slug. If the section exists, strip it and the Summary-table row. Leaves
# the rest of the file untouched.
BACKLOG_FILE="$REPO_ROOT/spec/changes/backlog.md"
if [ -f "$BACKLOG_FILE" ] && grep -qE "^## $CHANGE_ID$" "$BACKLOG_FILE"; then
  python3 - "$BACKLOG_FILE" "$CHANGE_ID" <<'PY'
import re, sys
path, slug = sys.argv[1], sys.argv[2]
text = open(path).read()
# Remove the Summary-table row: `| N | [slug](#slug) | ... |`
text = re.sub(rf"^\| \d+ \| \[{re.escape(slug)}\]\(#{re.escape(slug)}\)[^\n]*\n", "", text, flags=re.MULTILINE)
# Remove the H2 block: `## slug\n` through the next `## ` or `---\n\n`
# Simplest: split on H2, drop the matching chunk.
parts = re.split(r"(?m)^## ", text)
kept = [parts[0]]
for p in parts[1:]:
    head = p.split("\n", 1)[0].strip()
    if head == slug:
        continue
    kept.append(p)
text = ("## ").join(kept)
open(path, "w").write(text)
PY
  git -C "$REPO_ROOT" add "$BACKLOG_FILE"
  git -C "$REPO_ROOT" commit -m "cleanup: remove $CHANGE_ID from backlog.md" >/dev/null 2>&1 || true
fi

printf '%s\n' "{\"archive_record\": {\"archived_at\": \"$ARCHIVED_AT\", \"archive_path\": \"$ARCHIVE_PATH\", \"commit_sha\": \"$SHA\"}}"
