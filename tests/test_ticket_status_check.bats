#!/usr/bin/env bats
# Tests for scripts/ticket-status-check.sh
#
# Strategy: stub curl (linear) or backlog (backlog) via PATH; per-test repo root
# carries spec/project.yaml with the desired ticketing: value.

SCRIPT_UNDER_TEST="$BATS_TEST_DIRNAME/../../scripts/ticket-status-check.sh"
ORCHESTRATOR_ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
STUB_DIR="$BATS_TMPDIR/stubs"
TEST_REPO="$BATS_TMPDIR/test-repo"

setup() {
  mkdir -p "$STUB_DIR" "$TEST_REPO/spec" "$TEST_REPO/config"
  cp "$ORCHESTRATOR_ROOT/config/ticket-status-map.yaml" "$TEST_REPO/config/"
  export PATH="$STUB_DIR:$PATH"
  unset LINEAR_API_KEY
}

teardown() {
  rm -rf "$STUB_DIR" "$TEST_REPO"
}

write_ticketing() {
  local backend="$1"
  printf 'version: 1\nticketing: %s\n' "$backend" > "$TEST_REPO/spec/project.yaml"
}

write_curl_stub() {
  local body="$1"
  cat > "$STUB_DIR/curl" <<STUB
#!/bin/sh
echo '$body'
STUB
  chmod +x "$STUB_DIR/curl"
}

write_curl_stub_fail() {
  cat > "$STUB_DIR/curl" <<'STUB'
#!/bin/sh
exit 22
STUB
  chmod +x "$STUB_DIR/curl"
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

linear_response() {
  local state_name="$1"
  printf '{"data":{"issue":{"state":{"name":"%s"}}}}' "$state_name"
}

@test "LINEAR ticketing + LINEAR_API_KEY unset -> warning, action=skip" {
  write_ticketing linear
  unset LINEAR_API_KEY
  run bash "$SCRIPT_UNDER_TEST" "ORC-99" "$TEST_REPO"
  [ "$status" -eq 0 ]
  [[ "$output" =~ "LINEAR_API_KEY" ]] || [[ "$output" =~ "skip" ]]
}

@test "linear: Todo status with no local state -> action=init phase=explore" {
  write_ticketing linear
  export LINEAR_API_KEY="test-key"
  write_curl_stub "$(linear_response 'Todo')"

  tmp_state_dir="$(mktemp -d)"
  export WORKFLOW_STATE_DIR="$tmp_state_dir"

  run bash "$SCRIPT_UNDER_TEST" "ORC-99" "$TEST_REPO"
  [ "$status" -eq 0 ]
  echo "$output" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['action']=='init', d; assert d.get('phase')=='explore', d; assert d.get('ticketing')=='linear', d"
}

@test "linear: In Progress + matching local state -> action=resume" {
  write_ticketing linear
  export LINEAR_API_KEY="test-key"
  write_curl_stub "$(linear_response 'In Progress')"

  tmp_state_dir="$(mktemp -d)"
  mkdir -p "$tmp_state_dir/orc-99"
  cat > "$tmp_state_dir/orc-99/state.yaml" <<'YAML'
schema: feature
flags: {}
status: active
YAML
  export WORKFLOW_STATE_DIR="$tmp_state_dir"

  run bash "$SCRIPT_UNDER_TEST" "ORC-99" "$TEST_REPO"
  [ "$status" -eq 0 ]
  echo "$output" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['action']=='resume', d"
}

@test "linear: In Progress + NO local state -> action=halt with checklist" {
  write_ticketing linear
  export LINEAR_API_KEY="test-key"
  write_curl_stub "$(linear_response 'In Progress')"

  tmp_state_dir="$(mktemp -d)"
  export WORKFLOW_STATE_DIR="$tmp_state_dir"

  run bash "$SCRIPT_UNDER_TEST" "ORC-99" "$TEST_REPO"
  echo "$output" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['action']=='halt', d; assert 'checklist' in d, d"
}

@test "linear: Done status -> action=halt with reason" {
  write_ticketing linear
  export LINEAR_API_KEY="test-key"
  write_curl_stub "$(linear_response 'Done')"

  export WORKFLOW_STATE_DIR="$(mktemp -d)"
  run bash "$SCRIPT_UNDER_TEST" "ORC-99" "$TEST_REPO"
  [ "$status" -eq 0 ]
  echo "$output" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['action']=='halt', d; assert 'reason' in d, d"
}

@test "linear: Cancelled status -> action=halt" {
  write_ticketing linear
  export LINEAR_API_KEY="test-key"
  write_curl_stub "$(linear_response 'Cancelled')"

  export WORKFLOW_STATE_DIR="$(mktemp -d)"
  run bash "$SCRIPT_UNDER_TEST" "ORC-99" "$TEST_REPO"
  echo "$output" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['action']=='halt', d"
}

@test "linear: API non-2xx -> action=skip" {
  write_ticketing linear
  export LINEAR_API_KEY="test-key"
  write_curl_stub_fail

  export WORKFLOW_STATE_DIR="$(mktemp -d)"
  run bash "$SCRIPT_UNDER_TEST" "ORC-99" "$TEST_REPO"
  [ "$status" -eq 0 ]
  echo "$output" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['action']=='skip', d" || \
    [[ "$output" =~ "skip" ]]
}

@test "backlog: To Do + no local state -> action=init phase=explore" {
  write_ticketing backlog
  write_backlog_stub "To Do"

  export WORKFLOW_STATE_DIR="$(mktemp -d)"
  run bash "$SCRIPT_UNDER_TEST" "task-42" "$TEST_REPO"
  [ "$status" -eq 0 ]
  echo "$output" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['action']=='init', d; assert d.get('phase')=='explore', d; assert d.get('ticketing')=='backlog', d"
}

@test "backlog: In Progress + matching state -> action=resume" {
  write_ticketing backlog
  write_backlog_stub "In Progress"

  tmp_state_dir="$(mktemp -d)"
  mkdir -p "$tmp_state_dir/task-42"
  echo 'schema: feature' > "$tmp_state_dir/task-42/state.yaml"
  export WORKFLOW_STATE_DIR="$tmp_state_dir"

  run bash "$SCRIPT_UNDER_TEST" "task-42" "$TEST_REPO"
  echo "$output" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['action']=='resume', d"
}

@test "backlog: CLI missing -> action=skip" {
  write_ticketing backlog
  # PATH without homebrew/npm — backlog CLI should not resolve
  export PATH="$STUB_DIR:/usr/bin:/bin"
  export WORKFLOW_STATE_DIR="$(mktemp -d)"
  run bash "$SCRIPT_UNDER_TEST" "task-42" "$TEST_REPO"
  [ "$status" -eq 0 ]
  [[ "$output" =~ "skip" ]]
  [[ "$stderr_output" =~ "ticketing backend unavailable" ]] || [[ "$output" =~ "ticketing backend unavailable" ]]
}

@test "backlog: Code Review -> action=resume phase run-phase-review" {
  write_ticketing backlog
  write_backlog_stub "Code Review"

  tmp_state_dir="$(mktemp -d)"
  mkdir -p "$tmp_state_dir/task-99"
  echo 'schema: feature' > "$tmp_state_dir/task-99/state.yaml"
  export WORKFLOW_STATE_DIR="$tmp_state_dir"

  run bash "$SCRIPT_UNDER_TEST" "task-99" "$TEST_REPO"
  echo "$output" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['action']=='resume', d; assert d.get('phase')=='run-phase-review', d"
}
