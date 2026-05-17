#!/usr/bin/env bash
# Test: per_agent_tokens covers every distinct agent in step_history
#
# AC-10: Given a complete fixture state.yaml representing a full autopilot/feature
# run, metrics.per_agent_tokens must contain one entry per distinct agent name
# found in step_history (not just the proxy/developer agent).
#
# This is a structural test: we run compute-swe-metrics.sh against a fixture
# and verify per_agent_tokens contains entries for all spawned agent types.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$REPO_ROOT/config/scripts/inline/compute-swe-metrics.sh"

pass=0
fail=0

check() {
  local desc="$1"
  local result="$2"
  if [[ "$result" -eq 0 ]]; then
    echo "PASS: $desc"
    ((pass++))
  else
    echo "FAIL: $desc"
    ((fail++))
  fi
}

check_contains() {
  local desc="$1"
  local haystack="$2"
  local needle="$3"
  if echo "$haystack" | grep -q "$needle"; then
    echo "PASS: $desc"
    ((pass++))
  else
    echo "FAIL: $desc — output does not contain '$needle'"
    ((fail++))
  fi
}

echo "=== Test: per_agent_tokens covers all agent types ==="

[[ -f "$SCRIPT" ]]
check "compute-swe-metrics.sh exists" $?
if [[ ! -f "$SCRIPT" ]]; then
  echo "Results: $pass passed, $fail failed"
  [[ "$fail" -eq 0 ]]; exit $?
fi

TMPDIR_BASE="${TMPDIR:-/tmp}/test-per-agent-$$"
mkdir -p "$TMPDIR_BASE"
cleanup() { rm -rf "$TMPDIR_BASE"; }
trap cleanup EXIT

STATE_DIR="$TMPDIR_BASE/state"
mkdir -p "$STATE_DIR"

# Fixture with multiple distinct agent types: developer, reviewer, inline
cat > "$STATE_DIR/state.yaml" <<'STATEYAML'
change_id: test-per-agent-001
slug: test-per-agent-001
schema: feature
status: completed
started_at: "2026-01-15T10:00:00Z"
completed_at: "2026-01-15T11:00:00Z"
step_history:
  - step_id: execute-next-task
    phase: implement
    status: completed
    agent: developer
    started_at: "2026-01-15T10:00:00Z"
    completed_at: "2026-01-15T10:30:00Z"
    usage:
      input_tokens: 2000
      output_tokens: 800
      total_tokens: 2800
      tool_uses: 10
      duration_ms: 30000
  - step_id: run-phase-review
    phase: implement
    status: completed
    agent: reviewer
    started_at: "2026-01-15T10:30:00Z"
    completed_at: "2026-01-15T10:45:00Z"
    usage:
      input_tokens: 1500
      output_tokens: 600
      total_tokens: 2100
      tool_uses: 4
      duration_ms: 15000
  - step_id: mark-change-completed
    phase: complete
    status: completed
    agent: inline
    started_at: "2026-01-15T10:45:00Z"
    completed_at: "2026-01-15T10:45:05Z"
    usage:
      total_tokens: 0
      tool_uses: 2
      duration_ms: 5000
STATEYAML

cat > "$STATE_DIR/tasks.md" <<'TASKSMD'
- [x] T-1: Task one
TASKSMD

OUTPUT=$(bash "$SCRIPT" "$STATE_DIR" 2>/dev/null)
EXIT_CODE=$?

check "script exits 0" "$([[ $EXIT_CODE -eq 0 ]] && echo 0 || echo 1)"

# Extract per_agent_tokens value
PER_AGENT=$(echo "$OUTPUT" | grep "per_agent_tokens:" | sed "s/.*per_agent_tokens: '//; s/'$//")
echo ""
echo "per_agent_tokens: $PER_AGENT"

# Verify all three agent types appear in per_agent_tokens
check_contains "per_agent_tokens has 'developer' entry" "$PER_AGENT" "developer"
check_contains "per_agent_tokens has 'reviewer' entry" "$PER_AGENT" "reviewer"
check_contains "per_agent_tokens has 'inline' entry" "$PER_AGENT" "inline"

# The inline agent (total_tokens=0) appears only if the per-agent awk handles
# the inline case: entries with agent: inline but total_tokens: 0 are currently
# skipped by the existing awk (it requires total_tokens > 0)
# This test will FAIL if the awk still skips zero-token entries (RED state for T-18)
# and PASS after T-19 fixes the awk (GREEN state)

echo ""
echo "Results: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
