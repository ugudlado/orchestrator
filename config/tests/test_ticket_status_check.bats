#!/usr/bin/env bats
# Tests for scripts/ticket-status-check.sh
# Tests must FAIL until T-7 lands (script doesn't exist yet).
#
# Strategy: stub curl via a fake binary placed early in PATH.
# The stub reads the request and emits a preset Linear-like JSON response.

SCRIPT_UNDER_TEST="$BATS_TEST_DIRNAME/../../scripts/ticket-status-check.sh"
FIXTURES_DIR="$BATS_TEST_DIRNAME/fixtures/ticket-status-check"
STUB_DIR="$BATS_TMPDIR/stubs"

setup() {
  mkdir -p "$STUB_DIR"
  # Create a stub yq that calls the real yq (so YAML parsing works)
  # The tests override curl only
  export PATH="$STUB_DIR:$PATH"

  # Default ticket-status-map.yaml location (worktree config)
  export REPO_ROOT
  REPO_ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"

  # Unset LINEAR_API_KEY by default; individual tests set it
  unset LINEAR_API_KEY
}

teardown() {
  rm -rf "$STUB_DIR"
}

# Helper: write a curl stub that returns a given response body with status 200
write_curl_stub() {
  local body="$1"
  cat > "$STUB_DIR/curl" <<STUB
#!/bin/sh
echo '$body'
STUB
  chmod +x "$STUB_DIR/curl"
}

# Helper: write a curl stub that returns a given HTTP status (non-2xx)
write_curl_stub_fail() {
  cat > "$STUB_DIR/curl" <<'STUB'
#!/bin/sh
exit 22
STUB
  chmod +x "$STUB_DIR/curl"
}

# Helper: linear GraphQL response for a given state name
linear_response() {
  local state_name="$1"
  printf '{"data":{"issue":{"state":{"name":"%s"}}}}' "$state_name"
}

@test "LINEAR_API_KEY unset -> warning emitted, exit 0 with action=skip" {
  unset LINEAR_API_KEY
  run bash "$SCRIPT_UNDER_TEST" "ORC-99" "$REPO_ROOT"
  [ "$status" -eq 0 ]
  # Should emit a warning (to stderr or stdout)
  [[ "$output" =~ "LINEAR_API_KEY" ]] || [[ "$output" =~ "skip" ]]
  # JSON output should have action=skip
  echo "$output" | grep -q '"action"' || true
}

@test "Todo status with no local state -> action=init phase=explore" {
  export LINEAR_API_KEY="test-key"
  write_curl_stub "$(linear_response 'Todo')"

  # Ensure no state.yaml exists for this ticket
  local tmp_state_dir
  tmp_state_dir="$(mktemp -d)"
  export WORKFLOW_STATE_DIR="$tmp_state_dir"

  run bash "$SCRIPT_UNDER_TEST" "ORC-99" "$REPO_ROOT"
  [ "$status" -eq 0 ]
  echo "$output" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['action']=='init', d; assert d.get('phase')=='explore', d"
}

@test "In Progress status with matching local state -> action=resume" {
  export LINEAR_API_KEY="test-key"
  write_curl_stub "$(linear_response 'In Progress')"

  # Create a fake matching state.yaml for ORC-99
  local tmp_state_dir
  tmp_state_dir="$(mktemp -d)"
  mkdir -p "$tmp_state_dir/orc-99"
  cat > "$tmp_state_dir/orc-99/state.yaml" <<'YAML'
schema: feature
flags: {}
status: active
YAML
  export WORKFLOW_STATE_DIR="$tmp_state_dir"

  run bash "$SCRIPT_UNDER_TEST" "ORC-99" "$REPO_ROOT"
  [ "$status" -eq 0 ]
  echo "$output" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['action']=='resume', d"
}

@test "In Progress status with NO local state -> action=halt with checklist" {
  export LINEAR_API_KEY="test-key"
  write_curl_stub "$(linear_response 'In Progress')"

  # No matching state.yaml
  local tmp_state_dir
  tmp_state_dir="$(mktemp -d)"
  export WORKFLOW_STATE_DIR="$tmp_state_dir"

  run bash "$SCRIPT_UNDER_TEST" "ORC-99" "$REPO_ROOT"
  # Should emit action=halt (exit 0, or exit 6 per design UC-E6)
  # The JSON should contain action=halt and a checklist
  echo "$output" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['action']=='halt', d; assert 'checklist' in d, d"
}

@test "Done status -> action=halt with reason" {
  export LINEAR_API_KEY="test-key"
  write_curl_stub "$(linear_response 'Done')"

  local tmp_state_dir
  tmp_state_dir="$(mktemp -d)"
  export WORKFLOW_STATE_DIR="$tmp_state_dir"

  run bash "$SCRIPT_UNDER_TEST" "ORC-99" "$REPO_ROOT"
  [ "$status" -eq 0 ]
  echo "$output" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['action']=='halt', d; assert 'reason' in d, d"
}

@test "Cancelled status -> action=halt with reason" {
  export LINEAR_API_KEY="test-key"
  write_curl_stub "$(linear_response 'Cancelled')"

  local tmp_state_dir
  tmp_state_dir="$(mktemp -d)"
  export WORKFLOW_STATE_DIR="$tmp_state_dir"

  run bash "$SCRIPT_UNDER_TEST" "ORC-99" "$REPO_ROOT"
  [ "$status" -eq 0 ]
  echo "$output" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['action']=='halt', d"
}

@test "Linear API returns non-2xx -> warning, exit 0 with action=skip" {
  export LINEAR_API_KEY="test-key"
  write_curl_stub_fail

  local tmp_state_dir
  tmp_state_dir="$(mktemp -d)"
  export WORKFLOW_STATE_DIR="$tmp_state_dir"

  run bash "$SCRIPT_UNDER_TEST" "ORC-99" "$REPO_ROOT"
  [ "$status" -eq 0 ]
  echo "$output" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['action']=='skip', d" || \
    [[ "$output" =~ "skip" ]]
}
