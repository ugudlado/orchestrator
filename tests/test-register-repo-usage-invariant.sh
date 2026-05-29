#!/usr/bin/env bash
# test-register-repo-usage-invariant.sh
#
# Tests the register-repo.sh step_history ingestion invariant:
# rows where agent != null AND agent != "inline" AND status = "completed"
# AND total_tokens IS NULL must be rejected with a warning to stderr.
#
# Invariant defined in spec.md FR-11 and design.md §9.
#
# Usage: bash tests/test-register-repo-usage-invariant.sh

set -uo pipefail

REPO_ROOT_MAIN="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$REPO_ROOT_MAIN/config/scripts/register-repo.sh"

# ── Test infrastructure ──────────────────────────────────────────────────
PASS=0
FAIL=0

pass() { echo "PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "FAIL: $1"; FAIL=$((FAIL + 1)); }

# ── Setup: temp DB + fake archive directory ──────────────────────────────
TEST_DB="$TMPDIR/test-register-repo-invariant-$$.duckdb"
FAKE_REPO="$TMPDIR/fake-repo-invariant-$$"
CHANGE_SLUG="test-invariant-change"
ARCHIVE_DIR="$FAKE_REPO/spec/changes/archive/$CHANGE_SLUG"

mkdir -p "$ARCHIVE_DIR"

# Fixture state.yaml: two step_history entries.
#   Entry 0 (valid):   agent=workflow-init, status=completed, usage has total_tokens=1100
#   Entry 1 (invalid): agent=developer,     status=completed, usage={} (no total_tokens)
cat > "$ARCHIVE_DIR/state.yaml" << EOF
change_id: $CHANGE_SLUG
repo_root: $FAKE_REPO
schema: feature
status: completed
started_at: "2024-01-01T00:00:00Z"
completed_at: "2024-01-01T02:00:00Z"
step_history:
  - step_id: workflow-init
    phase: specify
    status: completed
    agent: workflow-init
    started_at: "2024-01-01T00:00:00Z"
    completed_at: "2024-01-01T00:30:00Z"
    usage:
      input_tokens: 1000
      output_tokens: 100
      total_tokens: 1100
      tool_uses: 5
      duration_ms: 30000
  - step_id: some-step
    phase: implement
    status: completed
    agent: developer
    started_at: "2024-01-01T01:00:00Z"
    completed_at: "2024-01-01T01:30:00Z"
    usage: {}
EOF

# ── Run register-repo.sh ─────────────────────────────────────────────────
STDERR_FILE="$TMPDIR/test-register-repo-invariant-$$.stderr"

METRICS_DB="$TEST_DB" \
  ORCHESTRATOR_HOME="$REPO_ROOT_MAIN" \
  REPO_ROOT="$FAKE_REPO" \
  bash "$SCRIPT" 2>"$STDERR_FILE"

EXIT_CODE=$?

# ── Assertions ───────────────────────────────────────────────────────────

# 1. Script exited successfully (non-blocking on bad rows — design.md §9)
if [[ "$EXIT_CODE" -eq 0 ]]; then
  pass "register-repo.sh exited 0"
else
  fail "register-repo.sh exited $EXIT_CODE (expected 0)"
fi

# 2. Exactly 1 row in step_history (valid row kept, invalid row dropped)
ROW_COUNT=$(duckdb "$TEST_DB" -csv -noheader "SELECT COUNT(*) FROM step_history;" 2>/dev/null || echo "-1")
if [[ "$ROW_COUNT" -eq 1 ]]; then
  pass "step_history has exactly 1 row (invariant dropped the invalid row)"
else
  fail "step_history has $ROW_COUNT rows (expected 1 — valid row kept, invalid dropped)"
fi

# 3. The surviving row is the VALID one (step_id = workflow-init)
SURVIVING_STEP=$(duckdb "$TEST_DB" -csv -noheader "SELECT step_id FROM step_history LIMIT 1;" 2>/dev/null || echo "")
if [[ "$SURVIVING_STEP" == "workflow-init" ]]; then
  pass "surviving row is the valid entry (step_id=workflow-init)"
else
  fail "surviving row step_id='$SURVIVING_STEP' (expected 'workflow-init')"
fi

# 4. Stderr contains a warning mentioning the dropped step_id
STDERR_CONTENT=$(cat "$STDERR_FILE")
if echo "$STDERR_CONTENT" | grep -qi "warn\|warning\|skip" && echo "$STDERR_CONTENT" | grep -q "some-step"; then
  pass "stderr contains warning mentioning dropped step_id (some-step)"
else
  fail "stderr missing expected warning about 'some-step'. Actual stderr: $STDERR_CONTENT"
fi

# ── Cleanup ──────────────────────────────────────────────────────────────
rm -f "$TEST_DB" "$STDERR_FILE"
rm -rf "$FAKE_REPO"

# ── Summary ─────────────────────────────────────────────────────────────
echo ""
echo "Results: $PASS passed, $FAIL failed"
if [[ "$FAIL" -gt 0 ]]; then
  exit 1
fi
exit 0
