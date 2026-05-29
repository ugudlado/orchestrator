#!/usr/bin/env bats

RECONCILE="$BATS_TEST_DIRNAME/../../scripts/ticket-reconcile.sh"
ORCHESTRATOR_ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
TEST_REPO="$BATS_TMPDIR/test-repo"
STUB_DIR="$BATS_TMPDIR/stubs"

setup() {
  mkdir -p "$STUB_DIR" "$TEST_REPO/spec/changes/foo"
  export PATH="$STUB_DIR:/usr/bin:/bin"
}

teardown() {
  rm -rf "$STUB_DIR" "$TEST_REPO"
}

write_state() {
  local ticket_status="${1:-Code Review}"
  cat > "$TEST_REPO/spec/changes/foo/state.yaml" <<YAML
schema: feature
change_id: foo
ticket_id: task-42
ticket_status: $ticket_status
repo_root: $TEST_REPO
YAML
}

write_backlog_stub() {
  local status_name="$1"
  cat > "$STUB_DIR/backlog" <<STUB
#!/bin/sh
if [ "\$1" = "task" ] && [ "\$2" = "view" ]; then
  printf 'ID: %s\nStatus: %s\n' "\$3" "$status_name"
  exit 0
fi
exit 1
STUB
  chmod +x "$STUB_DIR/backlog"
}

@test "reconcile: Code Review -> In Progress sets rework on state" {
  write_state "Code Review"
  printf 'version: 1\nticketing: backlog\n' > "$TEST_REPO/spec/project.yaml"
  write_backlog_stub "In Progress"

  run bash "$RECONCILE" "$TEST_REPO/spec/changes/foo/state.yaml"
  [ "$status" -eq 0 ]
  echo "$output" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['action']=='rework' and d['ticket_rework'] is True, d"

  run python3 - "$TEST_REPO/spec/changes/foo/state.yaml" <<'PY'
import sys, yaml
with open(sys.argv[1]) as f:
    s = yaml.safe_load(f)
assert s.get("ticket_status") == "In Progress"
assert s.get("ticket_rework") is True
assert (s.get("flags") or {}).get("rework_from_review") is True
assert s.get("ticket_status_checked_at")
PY
  [ "$status" -eq 0 ]
}

@test "reconcile: same status -> updated not rework" {
  write_state "In Progress"
  printf 'version: 1\nticketing: backlog\n' > "$TEST_REPO/spec/project.yaml"
  write_backlog_stub "In Progress"

  run bash "$RECONCILE" "$TEST_REPO/spec/changes/foo/state.yaml"
  [ "$status" -eq 0 ]
  echo "$output" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['action']=='updated' and d['ticket_rework'] is False, d"
}
