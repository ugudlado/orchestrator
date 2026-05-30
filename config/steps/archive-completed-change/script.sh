#!/usr/bin/env bash
# archive-completed-change.sh — move workflow state to archive + commit.
#
# Env (injected by orchestrator): REPO_ROOT, CHANGE_ID, ARCHIVE_PATH,
#   WORKTREE_ROOT (when worktree_path is set; else empty)
# Outputs: {archive_record: ...} or {archive_record: {skipped: true, reason}}

set -uo pipefail

: "${REPO_ROOT:?orchestrator: REPO_ROOT required}"
: "${CHANGE_ID:?orchestrator: CHANGE_ID required}"
: "${ARCHIVE_PATH:?orchestrator: ARCHIVE_PATH required}"

ARCHIVE_PATH="${ARCHIVE_PATH%/}"

if [ -n "${WORKTREE_ROOT:-}" ]; then
  SRC="${WORKTREE_ROOT}/spec/changes/${CHANGE_ID}"
  GIT_ROOT="$WORKTREE_ROOT"
else
  SRC="${REPO_ROOT}/spec/changes/${CHANGE_ID}"
  GIT_ROOT="$REPO_ROOT"
fi
DST="${GIT_ROOT}/${ARCHIVE_PATH}"

if [ ! -d "$SRC" ]; then
  printf '%s\n' "{\"archive_record\": {\"skipped\": true, \"reason\": \"source dir missing: $SRC\"}}"
  exit 0
fi

if [ -e "$DST" ]; then
  printf '%s\n' "{\"archive_record\": {\"skipped\": true, \"reason\": \"archive destination already exists: $DST\"}}"
  exit 1
fi

mkdir -p "$(dirname "$DST")"
if ! mv "$SRC" "$DST"; then
  printf '%s\n' "{\"archive_record\": {\"skipped\": true, \"reason\": \"mv failed: $SRC -> $DST\"}}"
  exit 1
fi

ARCHIVED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)

cd "$GIT_ROOT"
git add "$ARCHIVE_PATH" 2>/dev/null
if [ -z "${WORKTREE_ROOT:-}" ]; then
  git add "spec/changes/${CHANGE_ID}" 2>/dev/null || true
fi
git commit -m "archive: $CHANGE_ID — complete phase artifacts" 2>/dev/null
SHA=$(git rev-parse HEAD 2>/dev/null || echo "")

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
