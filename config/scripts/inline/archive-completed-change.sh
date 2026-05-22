#!/usr/bin/env bash
# archive-completed-change.sh — move workflow state to repo archive + commit.
#
# Env inputs:  REPO_ROOT, CHANGE_ID, ARCHIVE_PATH
#              (ARCHIVE_PATH is relative to REPO_ROOT, e.g. "spec/changes/archive/2026-04-18-hl-287")
#              WORKTREE_ROOT — root of the feature worktree; falls back to
#                             ORCHESTRATOR_WORKFLOW_DIR (worktree_path from state.yaml).
#                             All files (state.yaml, plan.yaml, artifacts) live at
#                             $WORKTREE_ROOT/spec/changes/$CHANGE_ID/ — worktrees required.
# Outputs:     {archive_record: {archived_at, archive_path, commit_sha}}
#   or        {archive_record: {skipped: true, reason}}

set -uo pipefail

REPO_ROOT="${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null)}"
CHANGE_ID="${CHANGE_ID:-}"
ARCHIVE_PATH="${ARCHIVE_PATH:-}"
WORKTREE_ROOT="${WORKTREE_ROOT:-${ORCHESTRATOR_WORKFLOW_DIR:-}}"

if [[ -n "${STATE_YAML_PATH:-}" && -f "$STATE_YAML_PATH" ]]; then
  # shellcheck source=./_read_state_env.sh
  source "$(dirname "$0")/_read_state_env.sh"
  read_state_env "$STATE_YAML_PATH" CHANGE_ID ARCHIVE_PATH WORKTREE_ROOT REPO_ROOT
  REPO_ROOT="${REPO_ROOT:-}"
  WORKTREE_ROOT="${WORKTREE_ROOT:-${ORCHESTRATOR_WORKFLOW_DIR:-}}"
fi

if [ -z "$CHANGE_ID" ] || [ -z "$ARCHIVE_PATH" ]; then
  printf '%s\n' '{"archive_record": {"skipped": true, "reason": "missing required env vars"}}'
  exit 0
fi

if [ -z "$WORKTREE_ROOT" ]; then
  printf '%s\n' '{"archive_record": {"skipped": true, "reason": "WORKTREE_ROOT not set — worktrees required"}}'
  exit 0
fi

SRC="${WORKTREE_ROOT}/spec/changes/${CHANGE_ID}"
DST="$REPO_ROOT/$ARCHIVE_PATH"

if [ ! -d "$SRC" ]; then
  printf '%s\n' "{\"archive_record\": {\"skipped\": true, \"reason\": \"source dir missing: $SRC\"}}"
  exit 0
fi

mkdir -p "$DST"
# Harden the copy: a failed cp must exit non-zero BEFORE the rm, so the source
# dir is never deleted on a partial archive. set -e is not enabled, so an
# explicit guard is required.
if ! cp -a "$SRC"/. "$DST"/; then
  printf '%s\n' "{\"archive_record\": {\"skipped\": true, \"reason\": \"cp failed: $SRC -> $DST\"}}"
  exit 1
fi
rm -rf "$SRC"

ARCHIVED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# Write cost-summary.md into the archive dir before committing.
# cost-report.sh lives at repo-root scripts/, not config/scripts/.
COST_REPORT_SCRIPT="$REPO_ROOT/scripts/cost-report.sh"
if [ -f "$COST_REPORT_SCRIPT" ] && [ -n "${ORCHESTRATOR_HOME:-}" ]; then
  bash "$COST_REPORT_SCRIPT" --change-id "$CHANGE_ID" > "$DST/cost-summary.md" 2>/dev/null || true
fi

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
