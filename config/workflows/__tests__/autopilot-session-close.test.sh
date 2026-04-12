#!/usr/bin/env bash
# Test: autopilot session close — aggregate metrics rollup
# Tests: scripts/autopilot-session-rollup.sh
#
# Asserts that given a session state.yaml with 3 iterations (2 completed, 1 failed),
# running the rollup script produces a finalized state.yaml with:
#   - top-level schema: autopilot
#   - status: completed
#   - metrics.tokens.total = sum of iteration tokens
#   - metrics.resolution.iterations_completed: 2
#   - metrics.resolution.iterations_failed: 1
#   - metrics.resolution.iterations_empty: 0
#   - no review_scores key
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
ROLLUP="$REPO_ROOT/config/scripts/autopilot-session-rollup.sh"

pass=0
fail=0

check() {
  local desc="$1"
  local result="$2"
  local expected="$3"
  if [[ "$result" == "$expected" ]]; then
    echo "PASS: $desc"
    ((pass++))
  else
    echo "FAIL: $desc — got '$result', expected '$expected'"
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

check_absent() {
  local desc="$1"
  local haystack="$2"
  local needle="$3"
  if echo "$haystack" | grep -q "$needle"; then
    echo "FAIL: $desc — output should NOT contain '$needle' but does"
    ((fail++))
  else
    echo "PASS: $desc"
    ((pass++))
  fi
}

# Rollup script must exist
if [[ ! -f "$ROLLUP" ]]; then
  echo "FAIL: $ROLLUP does not exist"
  exit 1
fi

TMPDIR_BASE="${TMPDIR:-/tmp}/hl278-session-close-test-$$"
SESSION_ID="test-session-001"
SESSION_DIR="$TMPDIR_BASE/repo/spec/changes/archive/autopilot-$SESSION_ID"
SESSION_STATE="$SESSION_DIR/state.yaml"
mkdir -p "$SESSION_DIR"
cleanup() { rm -rf "$TMPDIR_BASE"; }
trap cleanup EXIT

# ── Create seed session state.yaml with 3 iterations ─────────────────────
# Iteration 1: completed, 5000 tokens
# Iteration 2: completed, 3000 tokens
# Iteration 3: failed, 1000 tokens
# Expected total: 9000 tokens
cat >"$SESSION_STATE" <<'STATEYAML'
change_id: autopilot-test-session-001
schema: autopilot
status: active
started_at: "2026-04-10T10:00:00Z"
iterations:
  - number: 1
    ticket: HL-100
    schema: feature
    status: completed
    started_at: "2026-04-10T10:00:00Z"
    completed_at: "2026-04-10T10:20:00Z"
    metrics:
      tokens:
        total: 5000
      duration_ms: 20000
      churn:
        files_changed: 3
  - number: 2
    ticket: HL-101
    schema: bugfix
    status: completed
    started_at: "2026-04-10T10:20:00Z"
    completed_at: "2026-04-10T10:40:00Z"
    metrics:
      tokens:
        total: 3000
      duration_ms: 20000
      churn:
        files_changed: 1
  - number: 3
    ticket: HL-102
    schema: feature
    status: failed
    started_at: "2026-04-10T10:40:00Z"
    completed_at: "2026-04-10T11:00:00Z"
    metrics:
      tokens:
        total: 1000
      duration_ms: 20000
      churn:
        files_changed: 0
STATEYAML

# ── Run the rollup ────────────────────────────────────────────────────────
REPO_ROOT="$TMPDIR_BASE/repo" bash "$ROLLUP" "$SESSION_ID" 2>/dev/null
EXIT_CODE=$?

check "rollup exits 0" "$EXIT_CODE" "0"

# ── Read finalized state.yaml ─────────────────────────────────────────────
FINAL=$(cat "$SESSION_STATE")

# Schema must be autopilot
SCHEMA_VAL=$(yq '.schema' "$SESSION_STATE")
check "schema: autopilot" "$SCHEMA_VAL" "autopilot"

# Status must be completed
STATUS_VAL=$(yq '.status' "$SESSION_STATE")
check "status: completed" "$STATUS_VAL" "completed"

# metrics.tokens.total must be 9000 (5000 + 3000 + 1000)
TOKENS_TOTAL=$(yq '.metrics.tokens.total' "$SESSION_STATE")
check "metrics.tokens.total = 9000" "$TOKENS_TOTAL" "9000"

# metrics.resolution.iterations_completed = 2
ITERS_COMPLETED=$(yq '.metrics.resolution.iterations_completed' "$SESSION_STATE")
check "iterations_completed = 2" "$ITERS_COMPLETED" "2"

# metrics.resolution.iterations_failed = 1
ITERS_FAILED=$(yq '.metrics.resolution.iterations_failed' "$SESSION_STATE")
check "iterations_failed = 1" "$ITERS_FAILED" "1"

# metrics.resolution.iterations_empty = 0
ITERS_EMPTY=$(yq '.metrics.resolution.iterations_empty' "$SESSION_STATE")
check "iterations_empty = 0" "$ITERS_EMPTY" "0"

# metrics.resolution.resolve_rate must be ~ (null)
RESOLVE_RATE=$(yq '.metrics.resolution.resolve_rate' "$SESSION_STATE")
check "resolve_rate is null" "$RESOLVE_RATE" "null"

# no review_scores key
check_absent "no review_scores key" "$FINAL" "review_scores:"

# metrics.churn.files_changed = 4 (3 + 1 + 0)
CHURN=$(yq '.metrics.churn.files_changed' "$SESSION_STATE")
check "churn.files_changed = 4" "$CHURN" "4"

echo ""
echo "Results: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
