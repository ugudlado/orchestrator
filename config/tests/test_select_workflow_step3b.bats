#!/usr/bin/env bats
# Tests for select-workflow.yaml step 3b integration.
# Tests must FAIL until T-11 (select-workflow.yaml edit) lands.
#
# Rationale: select-workflow.yaml step 3b is an LLM instruction block, not a
# shell script, so it cannot be bats-tested directly. Instead these tests:
#   1. Verify step 3b is present in the select-workflow.yaml file (structural).
#   2. Exercise ticket-status-check.sh (the underlying mechanism step 3b calls)
#      for the scenarios that step 3b must handle per design.

SCRIPT="$BATS_TEST_DIRNAME/../../scripts/ticket-status-check.sh"
SELECT_WORKFLOW_YAML="$BATS_TEST_DIRNAME/../../config/steps/select-workflow.yaml"
ORCHESTRATOR_ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
TEST_REPO="$BATS_TMPDIR/test-repo"
STUB_DIR="$BATS_TMPDIR/stubs"

setup() {
  mkdir -p "$STUB_DIR" "$TEST_REPO/spec" "$TEST_REPO/config"
  cp "$ORCHESTRATOR_ROOT/config/ticket-status-map.yaml" "$TEST_REPO/config/"
  printf 'version: 1\nticketing: linear\n' > "$TEST_REPO/spec/project.yaml"
  export PATH="$STUB_DIR:$PATH"
  unset LINEAR_API_KEY
}

write_curl_stub() {
  local state_name="$1"
  cat > "$STUB_DIR/curl" <<STUB
#!/bin/sh
printf '{"data":{"issue":{"state":{"name":"$state_name"}}}}'
STUB
  chmod +x "$STUB_DIR/curl"
}

teardown() {
  rm -rf "$STUB_DIR"
}

# --- Structural: step 3b must be present in select-workflow.yaml ---

@test "select-workflow.yaml contains step 3b" {
  # This test FAILS until T-11 inserts step 3b
  [ -f "$SELECT_WORKFLOW_YAML" ]
  grep -q "step 3b" "$SELECT_WORKFLOW_YAML"
}

@test "select-workflow.yaml references ticket-status-map.yaml" {
  [ -f "$SELECT_WORKFLOW_YAML" ]
  grep -q "ticket-status-map.yaml" "$SELECT_WORKFLOW_YAML"
}

# --- Behavioral: ticket-driven init (Todo + no local state) ---

@test "Ticket-driven init: Todo + no local state -> action=init at explore phase" {
  export LINEAR_API_KEY="test-key"
  write_curl_stub "Todo"

  local tmp_state_dir
  tmp_state_dir="$(mktemp -d)"
  export WORKFLOW_STATE_DIR="$tmp_state_dir"

  run bash "$SCRIPT" "ORC-99" "$TEST_REPO"
  [ "$status" -eq 0 ]
  echo "$output" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert d['action'] == 'init', f'Expected action=init, got {d}'
assert d.get('phase') == 'explore', f'Expected phase=explore, got {d}'
"
}

# --- Behavioral: ticket-driven resume (In Progress + matching state) ---

@test "Ticket-driven resume: In Progress + matching state -> action=resume" {
  export LINEAR_API_KEY="test-key"
  write_curl_stub "In Progress"

  local tmp_state_dir
  tmp_state_dir="$(mktemp -d)"
  mkdir -p "$tmp_state_dir/orc-99"
  cat > "$tmp_state_dir/orc-99/state.yaml" <<'YAML'
schema: feature
flags: {}
status: active
YAML
  export WORKFLOW_STATE_DIR="$tmp_state_dir"

  run bash "$SCRIPT" "ORC-99" "$TEST_REPO"
  [ "$status" -eq 0 ]
  echo "$output" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert d['action'] == 'resume', f'Expected action=resume, got {d}'
"
}

# --- Behavioral: In Review + matching state resumes at run-phase-review ---

@test "In Review + matching state -> action=resume at run-phase-review phase" {
  export LINEAR_API_KEY="test-key"
  write_curl_stub "In Review"

  local tmp_state_dir
  tmp_state_dir="$(mktemp -d)"
  mkdir -p "$tmp_state_dir/orc-99"
  cat > "$tmp_state_dir/orc-99/state.yaml" <<'YAML'
schema: feature
flags: {}
status: active
YAML
  export WORKFLOW_STATE_DIR="$tmp_state_dir"

  run bash "$SCRIPT" "ORC-99" "$TEST_REPO"
  [ "$status" -eq 0 ]
  echo "$output" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert d['action'] == 'resume', f'Expected action=resume, got {d}'
assert d.get('phase') == 'run-phase-review', f'Expected phase=run-phase-review, got {d}'
"
}

# --- Behavioral: Mid-status + no local state -> halt with checklist ---

@test "Mid-status + no local state -> action=halt with checklist" {
  export LINEAR_API_KEY="test-key"
  write_curl_stub "In Progress"

  local tmp_state_dir
  tmp_state_dir="$(mktemp -d)"
  export WORKFLOW_STATE_DIR="$tmp_state_dir"

  run bash "$SCRIPT" "ORC-99" "$TEST_REPO"
  [ "$status" -eq 0 ]
  echo "$output" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert d['action'] == 'halt', f'Expected action=halt, got {d}'
assert 'checklist' in d, f'Expected checklist in {d}'
"
}

# --- Behavioral: Done/Cancelled -> warns and halts ---

@test "Done status -> action=halt" {
  export LINEAR_API_KEY="test-key"
  write_curl_stub "Done"

  local tmp_state_dir
  tmp_state_dir="$(mktemp -d)"
  export WORKFLOW_STATE_DIR="$tmp_state_dir"

  run bash "$SCRIPT" "ORC-99" "$TEST_REPO"
  [ "$status" -eq 0 ]
  echo "$output" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert d['action'] == 'halt', f'Expected action=halt, got {d}'
"
}
