#!/usr/bin/env bash
# complete-workflow.sh — single terminal workflow-teardown step.
#
# Replaces the former archive-completed-change → merge-to-main → remove-worktree
# step trio. Sequences, inside ONE process so no `orchestrator next` dispatch
# boundary ever sits between a state-moving and a state-reading operation:
#
#   0. READ all state.yaml-derived values into bash vars (before any mutation)
#   1. MERGE   (if flags.merge_to_main)  — runs while CWD is inside the worktree
#   2. ARCHIVE (unconditional)           — moves state.yaml/tasks.md out of the
#                                          worktree to the repo archive
#   3. cd "$REPO_ROOT"                   — git worktree remove fails if CWD is
#                                          inside the target worktree
#      CLEANUP (if flags.worktree)       — git worktree remove + branch delete
#
# The three helper scripts (merge-to-main.sh, archive-completed-change.sh,
# remove-worktree.sh) hold the git/filesystem logic. They are invoked as
# `bash <script>` SUBPROCESSES, not source'd — they use `set -uo pipefail` and
# `exit` on early-return paths, so sourcing them would terminate this wrapper.
#
# Env inputs:  STATE_YAML_PATH, REPO_ROOT  (forwarded by bin/orchestrator)
# Outputs:     {"completion_record": {merge_record, archive_record, worktree_record}}

set -uo pipefail

_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- Step 0: read ALL state.yaml-derived values into bash vars --------------
# Every read happens here, before archive moves state.yaml out of the worktree.
CHANGE_ID=""
ARCHIVE_PATH=""
WORKTREE_ROOT=""
WORKTREE_PATH=""
REPO_ROOT="${REPO_ROOT:-}"
BRANCH=""
MERGE_TO_MAIN=""
WORKTREE=""

if [[ -n "${STATE_YAML_PATH:-}" && -f "${STATE_YAML_PATH:-}" ]]; then
  # shellcheck source=./_read_state_env.sh
  source "$_DIR/_read_state_env.sh"
  read_state_env "$STATE_YAML_PATH" \
    CHANGE_ID ARCHIVE_PATH WORKTREE_ROOT WORKTREE_PATH REPO_ROOT BRANCH \
    MERGE_TO_MAIN WORKTREE
fi

REPO_ROOT="${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null)}"
WORKTREE_PATH="${WORKTREE_PATH/#\~/$HOME}"

# JSON-escape a string for embedding inside a record value.
_json_escape() {
  printf '%s' "$1" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'
}

# --- Step 1: merge (gated on flags.merge_to_main) ---------------------------
# Runs with CWD still inside the worktree.
MERGE_RECORD=""
if [[ "$MERGE_TO_MAIN" == "true" || "$MERGE_TO_MAIN" == "True" ]]; then
  _merge_out="$(bash "$_DIR/merge-to-main.sh")"
  _merge_rc=$?
  if [[ $_merge_rc -ne 0 ]]; then
    # Merge conflict (or checkout failure): halt before archive + cleanup.
    printf '%s\n' "$_merge_out" >&2
    printf '%s\n' "{\"completion_record\": {\"merge_record\": {\"merged\": false, \"skipped\": false, \"reason\": \"merge failed (exit $_merge_rc)\"}}}"
    exit "$_merge_rc"
  fi
  # merge-to-main.sh emits {"merge_record": {...}}; keep the inner object.
  MERGE_RECORD="$(printf '%s' "$_merge_out" | python3 -c 'import json,sys
try:
    obj = json.loads(sys.stdin.read() or "{}")
except Exception:
    obj = {}
print(json.dumps(obj.get("merge_record", {})))')"
else
  MERGE_RECORD='{"skipped": true, "reason": "merge_to_main flag false"}'
fi

# --- Step 2: archive (unconditional) ----------------------------------------
# Moves state.yaml/tasks.md/artifacts to $REPO_ROOT/$ARCHIVE_PATH. After this
# point STATE_YAML_PATH no longer exists — every value needed is already in a
# bash var from step 0.
_archive_out="$(bash "$_DIR/archive-completed-change.sh")"
_archive_rc=$?
ARCHIVE_RECORD="$(printf '%s' "$_archive_out" | python3 -c 'import json,sys
try:
    obj = json.loads(sys.stdin.read() or "{}")
except Exception:
    obj = {}
print(json.dumps(obj.get("archive_record", {})))')"
if [[ $_archive_rc -ne 0 ]]; then
  # Archive cp failed before its rm -rf; source dir intact. Halt cleanup.
  printf '%s\n' "$_archive_out" >&2
  printf '%s\n' "{\"completion_record\": {\"merge_record\": $MERGE_RECORD, \"archive_record\": $ARCHIVE_RECORD}}"
  exit "$_archive_rc"
fi

# --- Step 3: cd out, then cleanup (gated on flags.worktree) -----------------
# `cd "$REPO_ROOT"` is critical: `git worktree remove` fails if CWD is inside
# the target worktree, and CWD started inside it.
cd "$REPO_ROOT" || exit 1

WORKTREE_RECORD=""
if [[ "$WORKTREE" == "true" || "$WORKTREE" == "True" ]]; then
  # Re-export the captured values so remove-worktree.sh reads them even though
  # STATE_YAML_PATH is now gone (archive moved it).
  _wt_out="$(STATE_YAML_PATH="" REPO_ROOT="$REPO_ROOT" \
            WORKTREE_PATH="$WORKTREE_PATH" BRANCH="$BRANCH" \
            bash "$_DIR/remove-worktree.sh")"
  WORKTREE_RECORD="$(printf '%s' "$_wt_out" | python3 -c 'import json,sys
try:
    obj = json.loads(sys.stdin.read() or "{}")
except Exception:
    obj = {}
print(json.dumps(obj))')"
else
  WORKTREE_RECORD='{"skipped": true, "reason": "worktree flag false"}'
fi

printf '%s\n' "{\"completion_record\": {\"merge_record\": $MERGE_RECORD, \"archive_record\": $ARCHIVE_RECORD, \"worktree_record\": $WORKTREE_RECORD}}"
