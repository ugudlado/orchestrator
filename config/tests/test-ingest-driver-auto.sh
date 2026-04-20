#!/usr/bin/env bash
# T-23: RED + T-24: GREEN test for complete-phase ingest-driver auto-invoke.
#
# Verifies that scripts/inline/ingest-driver-auto.py:
#   1. Resolves session_id from TMPDIR (primary path — UUID directory component)
#   2. Resolves session_id via JSONL scan (fallback path — newest file in window)
#   3. Writes a driver-loop row to step_events via orchestrator ingest-driver
#   4. Fails soft (exits 0, emits skipped=true) when session_id is unresolvable
#
# Uses a real existing JSONL in ~/.claude/projects/-Users-spidey-code-orchestrator/
# as the fixture. The repo_root in state.yaml points to the REAL orchestrator repo
# (/Users/spidey/code/orchestrator) so ingest-driver can locate the JSONL via
# Path.home() / ".claude" / "projects" / "<slug>" without any HOME override.
#
# All DuckDB writes go to a temp file (METRICS_DB) so real data is untouched.
#
# At RED time (T-23): script does not exist -> fails immediately.
# At GREEN time (T-24): all assertions pass.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/inline/ingest-driver-auto.py"

PASS=0
FAIL=0

pass() { echo "PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "FAIL: $1"; FAIL=$((FAIL + 1)); }

# ── Fixture: a real JSONL from ~/.claude/projects for the orchestrator repo ───
# Real orchestrator repo slug = -Users-spidey-code-orchestrator
REAL_REPO_ROOT="/Users/spidey/code/orchestrator"
REAL_SLUG="-Users-spidey-code-orchestrator"
# A known session UUID that has 105 assistant turns with usage (verified)
SESSION_ID="022bd471-bf04-4af2-8de7-f8a66622d330"
JSONL_PATH="$HOME/.claude/projects/$REAL_SLUG/$SESSION_ID.jsonl"

# ── Guard: real JSONL must exist for the test to work ─────────────────────────
if [[ ! -f "$JSONL_PATH" ]]; then
  echo "SKIP: fixture JSONL not found at $JSONL_PATH — test cannot run"
  echo "Results: 0 passed, 0 failed (skipped)"
  exit 0
fi

# ── Setup: temp directories + state.yaml fixture ──────────────────────────────
FAKE_BASE="${TMPDIR:-/tmp}/test-ingest-driver-auto-$$"
mkdir -p "$FAKE_BASE"
METRICS_DB_PATH="$FAKE_BASE/test.duckdb"
STATE_DIR="$FAKE_BASE/state"
mkdir -p "$STATE_DIR"

CHANGE_ID="test-ingest-driver-auto"

# State YAML: timestamps bracket the JSONL session (session was 2026-04-17T20:43..21:04)
cat > "$STATE_DIR/state.yaml" << YAML
change_id: $CHANGE_ID
repo_root: $REAL_REPO_ROOT
schema: feature
status: completed
started_at: "2026-04-17T20:00:00Z"
completed_at: "2026-04-17T22:00:00Z"
YAML

cleanup() {
  rm -rf "$FAKE_BASE"
}
trap cleanup EXIT

# ── Test 0: script must exist (RED gate) ──────────────────────────────────────
echo "=== Test: ingest-driver-auto step script exists ==="
if [[ -f "$SCRIPT" ]]; then
  pass "step script exists at $SCRIPT"
else
  fail "step script NOT found at $SCRIPT (T-23 RED — expected at T-24 GREEN)"
  echo ""
  echo "Results: $PASS passed, $FAIL failed"
  exit 1
fi

# ── Test 1: primary path — session_id from TMPDIR UUID component ──────────────
echo "=== Test: TMPDIR-based session_id resolution (primary path) ==="
# Simulate the TMPDIR that Claude Code sets during an agent session:
#   TMPDIR=/tmp/claude-501/<repo-slug-dir>/<session-uuid>/
FAKE_AGENT_TMPDIR="$FAKE_BASE/agenttmp/$REAL_SLUG/$SESSION_ID"
mkdir -p "$FAKE_AGENT_TMPDIR"

RESULT=$(TMPDIR="$FAKE_AGENT_TMPDIR" \
  METRICS_DB="$METRICS_DB_PATH" \
  ORCHESTRATOR_HOME="$REPO_ROOT" \
  python3 "$SCRIPT" "$STATE_DIR/state.yaml" 2>/dev/null)
EXIT_CODE=$?

if [[ "$EXIT_CODE" -eq 0 ]]; then
  pass "step exits 0 (primary TMPDIR path)"
else
  fail "step exited $EXIT_CODE (expected 0) — primary TMPDIR path"
fi

COUNT=$(duckdb "$METRICS_DB_PATH" -csv -noheader \
  "SELECT COUNT(*) FROM step_events WHERE change_id='$CHANGE_ID' AND agent_name='driver-loop';" \
  2>/dev/null || echo "0")

if [[ "$COUNT" -eq 1 ]]; then
  pass "step_events has exactly 1 driver-loop row (primary TMPDIR path)"
else
  fail "step_events has $COUNT driver-loop rows (expected 1 — primary TMPDIR path); stdout: $RESULT"
fi

# Verify row has non-null tokens + cost
HAS_TOKENS=$(duckdb "$METRICS_DB_PATH" -csv -noheader \
  "SELECT COUNT(*) FROM step_events WHERE change_id='$CHANGE_ID' AND agent_name='driver-loop' AND input_tokens IS NOT NULL AND output_tokens IS NOT NULL AND cost_usd IS NOT NULL;" \
  2>/dev/null || echo "0")

if [[ "$HAS_TOKENS" -eq 1 ]]; then
  pass "driver-loop row has non-null input_tokens, output_tokens, cost_usd"
else
  fail "driver-loop row missing required token/cost fields (HAS_TOKENS=$HAS_TOKENS)"
fi

# ── Test 2: fallback path — scan ~/.claude/projects for newest JSONL ──────────
echo "=== Test: fallback scan when TMPDIR has no UUID ==="
rm -f "$METRICS_DB_PATH"

# No UUID in TMPDIR -> script falls through to JSONL scan
RESULT=$(TMPDIR="$FAKE_BASE/no-uuid-dir" \
  METRICS_DB="$METRICS_DB_PATH" \
  ORCHESTRATOR_HOME="$REPO_ROOT" \
  python3 "$SCRIPT" "$STATE_DIR/state.yaml" 2>/dev/null)
EXIT_CODE=$?

if [[ "$EXIT_CODE" -eq 0 ]]; then
  pass "step exits 0 (fallback scan path)"
else
  fail "step exited $EXIT_CODE on fallback path (expected 0)"
fi

COUNT=$(duckdb "$METRICS_DB_PATH" -csv -noheader \
  "SELECT COUNT(*) FROM step_events WHERE change_id='$CHANGE_ID' AND agent_name='driver-loop';" \
  2>/dev/null || echo "0")

if [[ "$COUNT" -eq 1 ]]; then
  pass "step_events has exactly 1 driver-loop row (fallback scan path)"
else
  fail "step_events has $COUNT driver-loop rows (expected 1 — fallback scan); stdout: $RESULT"
fi

# ── Test 3: fail-soft when session_id is unresolvable ─────────────────────────
echo "=== Test: fail-soft when JSONL not found (no UUID, no projects dir) ==="
STDERR_FILE="$FAKE_BASE/test-stderr.txt"

# State.yaml pointing to a fake repo_root that has no JSONL in ~/.claude/projects
cat > "$STATE_DIR/state-no-jsonl.yaml" << YAML
change_id: $CHANGE_ID
repo_root: /tmp/nonexistent-repo-for-test
schema: feature
status: completed
started_at: "2026-04-19T09:00:00Z"
completed_at: "2026-04-19T12:00:00Z"
YAML

RESULT=$(TMPDIR="$FAKE_BASE/no-uuid-dir" \
  METRICS_DB="$FAKE_BASE/empty.duckdb" \
  ORCHESTRATOR_HOME="$REPO_ROOT" \
  python3 "$SCRIPT" "$STATE_DIR/state-no-jsonl.yaml" 2>"$STDERR_FILE")
EXIT_CODE=$?

if [[ "$EXIT_CODE" -eq 0 ]]; then
  pass "step exits 0 on unresolvable session_id (fail-soft)"
else
  fail "step exited $EXIT_CODE on unresolvable session_id (expected 0 — fail-soft)"
fi

SKIPPED=$(echo "$RESULT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('skipped', False))" 2>/dev/null || echo "")
if [[ "$SKIPPED" == "True" ]]; then
  pass "fail-soft output contains skipped=true"
else
  fail "fail-soft output missing skipped=true: got '$RESULT'"
fi

STDERR_CONTENT=$(cat "$STDERR_FILE" 2>/dev/null || echo "")
if echo "$STDERR_CONTENT" | grep -qi "warn\|session.id\|unresolvable\|skip"; then
  pass "stderr contains warning about unresolvable session_id"
else
  fail "stderr missing warning. Got: '$STDERR_CONTENT'"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "Results: $PASS passed, $FAIL failed"
if [[ "$FAIL" -gt 0 ]]; then
  exit 1
fi
exit 0
