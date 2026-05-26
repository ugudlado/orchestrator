#!/usr/bin/env bats
# record-issue.sh — sentinel JSONL helper (orc-89 T-2 RED).
# Expect failures until config/scripts/inline/record-issue.sh exists (T-3).

REPO_ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
RECORD_ISSUE="$REPO_ROOT/config/scripts/inline/record-issue.sh"
CHANGE_ID="orc-89-bats"

setup() {
  SANDBOX="$BATS_TMPDIR/record-issue-$$"
  mkdir -p "$SANDBOX/spec/changes/$CHANGE_ID"
  PENDING_FILE="$SANDBOX/spec/changes/$CHANGE_ID/.pending-issues.jsonl"
  export WORKTREE_PATH="$SANDBOX"
  export CHANGE_ID
  export PHASE="implement"
  export STEP_ID="task-T-2"
}

teardown() {
  rm -rf "$SANDBOX"
}

@test "record-issue.sh with all env+flags writes one JSON line with supplied fields" {
  run --separate-stderr bash "$RECORD_ISSUE" \
    --category telemetry \
    --severity workaround-applied \
    --detail "usage block empty" \
    --dedup-key "empty-usage:implement:task-T-2" \
    --workaround "continued anyway" \
    --fix-direction "pass agent_task_result to record.py"

  [ "$status" -eq 0 ]
  [ -f "$PENDING_FILE" ]

  python3 - "$PENDING_FILE" <<'PY'
import json
import sys

path = sys.argv[1]
lines = [ln for ln in open(path) if ln.strip()]
assert len(lines) == 1, f"expected 1 line, got {len(lines)}"
obj = json.loads(lines[0])
assert obj["category"] == "telemetry"
assert obj["severity"] == "workaround-applied"
assert obj["detail"] == "usage block empty"
assert obj["dedup_key"] == "empty-usage:implement:task-T-2"
assert obj["workaround"] == "continued anyway"
assert obj["fix_direction"] == "pass agent_task_result to record.py"
assert obj.get("surfaced_at") == "implement/task-T-2"
PY
}

@test "record-issue.sh with missing CHANGE_ID exits 0, warns on stderr, writes nothing" {
  unset CHANGE_ID

  run --separate-stderr bash "$RECORD_ISSUE" \
    --category tooling-bug \
    --severity cosmetic \
    --detail "missing change id"

  [ "$status" -eq 0 ]
  [[ "$stderr" == *CHANGE_ID* ]]
  [ ! -f "$PENDING_FILE" ]
}

@test "record-issue.sh with missing WORKTREE_PATH exits 0, warns on stderr, writes nothing" {
  unset WORKTREE_PATH

  run --separate-stderr bash "$RECORD_ISSUE" \
    --category tooling-bug \
    --severity cosmetic \
    --detail "missing worktree"

  [ "$status" -eq 0 ]
  [[ "$stderr" == *WORKTREE_PATH* ]]
  [ ! -f "$PENDING_FILE" ]
}

@test "record-issue.sh appends multiple lines without overwriting" {
  bash "$RECORD_ISSUE" \
    --category telemetry \
    --severity cosmetic \
    --detail "first" \
    --dedup-key "key-a"

  bash "$RECORD_ISSUE" \
    --category driver-bug \
    --severity workaround-applied \
    --detail "second" \
    --dedup-key "key-b"

  python3 - "$PENDING_FILE" <<'PY'
import json
import sys

lines = [ln.strip() for ln in open(sys.argv[1]) if ln.strip()]
assert len(lines) == 2, f"expected 2 lines, got {len(lines)}"
first, second = (json.loads(ln) for ln in lines)
assert first["dedup_key"] == "key-a"
assert second["dedup_key"] == "key-b"
PY
}
