#!/usr/bin/env bats

SCRIPT="$BATS_TEST_DIRNAME/../../scripts/ticket-sync.sh"
ORCHESTRATOR_ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
TEST_REPO="$BATS_TMPDIR/test-repo"
STUB_DIR="$BATS_TMPDIR/stubs"

setup() {
  mkdir -p "$STUB_DIR" "$TEST_REPO/spec" "$TEST_REPO/config"
  cp "$ORCHESTRATOR_ROOT/config/ticket-step-sync.yaml" "$TEST_REPO/config/"
  export PATH="$STUB_DIR:/usr/bin:/bin"
}

teardown() {
  rm -rf "$STUB_DIR" "$TEST_REPO"
}

write_state() {
  local ticket_id="$1"
  cat > "$TEST_REPO/spec/changes/foo/state.yaml" <<YAML
schema: feature
change_id: foo
ticket_id: $ticket_id
repo_root: $TEST_REPO
YAML
}

write_backlog_stub() {
  cat > "$STUB_DIR/backlog" <<'STUB'
#!/bin/sh
echo "$@" >> "$BATS_TMPDIR/backlog_calls.log"
exit 0
STUB
  chmod +x "$STUB_DIR/backlog"
  : > "$BATS_TMPDIR/backlog_calls.log"
}

@test "backlog: run-phase-review maps to Code Review" {
  mkdir -p "$TEST_REPO/spec/changes/foo"
  write_state "task-42"
  printf 'version: 1\nticketing: backlog\n' > "$TEST_REPO/spec/project.yaml"
  write_backlog_stub

  run bash "$SCRIPT" "$TEST_REPO/spec/changes/foo/state.yaml" "run-phase-review"
  [ "$status" -eq 0 ]
  grep -q 'task edit task-42' "$BATS_TMPDIR/backlog_calls.log"
  grep -q 'Code Review' "$BATS_TMPDIR/backlog_calls.log"
}

@test "no mapping for step -> no backlog call" {
  mkdir -p "$TEST_REPO/spec/changes/foo"
  write_state "task-42"
  printf 'version: 1\nticketing: backlog\n' > "$TEST_REPO/spec/project.yaml"
  write_backlog_stub

  run bash "$SCRIPT" "$TEST_REPO/spec/changes/foo/state.yaml" "expand-plan"
  [ "$status" -eq 0 ]
  [ ! -s "$BATS_TMPDIR/backlog_calls.log" ]
}

@test "pattern:task-* maps to In Progress" {
  mkdir -p "$TEST_REPO/spec/changes/foo"
  write_state "task-7"
  printf 'version: 1\nticketing: backlog\n' > "$TEST_REPO/spec/project.yaml"
  write_backlog_stub

  run bash "$SCRIPT" "$TEST_REPO/spec/changes/foo/state.yaml" "task-T-3"
  [ "$status" -eq 0 ]
  grep -q 'In Progress' "$BATS_TMPDIR/backlog_calls.log"
}
