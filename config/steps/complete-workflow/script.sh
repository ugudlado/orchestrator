#!/usr/bin/env bash
# complete-workflow.sh — archive step for workflow completion.
#
# Sequences, inside ONE process:
#   0. READ state.yaml-derived values into bash vars (before any mutation)
#   1. ARCHIVE (unconditional) — mv active session dir to archive on the feature
#      worktree when worktree=true, else in REPO_ROOT
#
# Merge and worktree removal run from `orchestrator complete` after the complete
# phase succeeds: merge first (unconditional — invoking that verb is the signal), then teardown.
#
# Env inputs:  STATE_YAML_PATH, REPO_ROOT  (forwarded by bin/orchestrator)
# Outputs:     {"completion_record": {merge_record, archive_record, worktree_record}}

set -uo pipefail

_ORC_HOME="${ORCHESTRATOR_HOME:-${REPO_ROOT}}"
_INLINE_DIR="${_ORC_HOME}/scripts/inline"
_ARCHIVE_SCRIPT="${_ORC_HOME}/config/steps/archive-completed-change/script.sh"

if [[ -n "${STATE_YAML_PATH:-}" && -f "${STATE_YAML_PATH:-}" ]]; then
  # shellcheck source=scripts/inline/_read_state_env.sh
  source "$_INLINE_DIR/_read_state_env.sh"
  read_state_env "$STATE_YAML_PATH" REPO_ROOT
fi

REPO_ROOT="${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null)}"

_extract_record() {
  local _key="$1"
  python3 -c '
import json, sys
key = sys.argv[1]
found = {}
for line in sys.stdin:
    line = line.strip()
    if not (line.startswith("{") and line.endswith("}")):
        continue
    try:
        obj = json.loads(line)
    except Exception:
        continue
    if isinstance(obj, dict):
        found = obj
print(json.dumps(found.get(key, {}) if key else found))
' "$_key"
}

MERGE_RECORD='{"skipped": true, "reason": "merge deferred to orchestrator complete (after archive)"}'
WORKTREE_RECORD='{"skipped": true, "reason": "worktree removal deferred to orchestrator complete (after merge)"}'

_archive_out="$(bash "$_ARCHIVE_SCRIPT")"
_archive_rc=$?

cd "$REPO_ROOT" || exit 1

ARCHIVE_RECORD="$(printf '%s\n' "$_archive_out" | _extract_record archive_record)"
if [[ $_archive_rc -ne 0 ]]; then
  printf '%s\n' "$_archive_out" >&2
  printf '%s\n' "{\"completion_record\": {\"merge_record\": $MERGE_RECORD, \"archive_record\": $ARCHIVE_RECORD}}"
  exit "$_archive_rc"
fi

printf '%s\n' "{\"completion_record\": {\"merge_record\": $MERGE_RECORD, \"archive_record\": $ARCHIVE_RECORD, \"worktree_record\": $WORKTREE_RECORD}}"
