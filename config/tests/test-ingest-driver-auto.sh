#!/usr/bin/env bash
# T-23: RED test for complete-phase ingest-driver auto-invoke.
#
# This test verifies that the ingest-driver-auto inline step:
#   1. Resolves a session_id from TMPDIR (primary path)
#   2. Falls back to scanning ~/.claude/projects/<slug>/*.jsonl (fallback path)
#   3. Calls `orchestrator ingest-driver` to write a driver-loop row to step_events
#   4. Emits the expected JSON result
#   5. Fails soft (exits 0) when session_id cannot be resolved
#
# At RED time: the step script scripts/inline/ingest-driver-auto.py does not
# exist yet, so the test should fail.
#
# Coverage: T-23, T-24
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/inline/ingest-driver-auto.py"
INGEST_DRIVER_BIN="$REPO_ROOT/bin/orchestrator"

PASS=0
FAIL=0

pass() { echo "PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "FAIL: $1"; FAIL=$((FAIL + 1)); }

# ── Setup: temp directories ──────────────────────────────────────────────────
FAKE_BASE="${TMPDIR:-/tmp}/test-ingest-driver-auto-$$"
FAKE_HOME="$FAKE_BASE/home"
FAKE_REPO="$FAKE_BASE/repo"
METRICS_DB_PATH="$FAKE_BASE/test.duckdb"
STATE_DIR="$FAKE_BASE/state"
SESSION_ID="b5cd958c-57b1-40a6-a184-e801a21abc30"

mkdir -p "$FAKE_HOME" "$FAKE_REPO" "$STATE_DIR"

# Initialize a real git repo so `git rev-parse --show-toplevel` returns FAKE_REPO
git -C "$FAKE_REPO" init -q
git -C "$FAKE_REPO" commit --allow-empty -m "init" -q

# Compute repo slug: absolute path with '/' replaced by '-'
REPO_SLUG=$(echo "$FAKE_REPO" | tr '/' '-')

# Seed JSONL at $FAKE_HOME/.claude/projects/<repo-slug>/<session-id>.jsonl
PROJECTS_DIR="$FAKE_HOME/.claude/projects/$REPO_SLUG"
mkdir -p "$PROJECTS_DIR"
cat > "$PROJECTS_DIR/$SESSION_ID.jsonl" << 'JSONL'
{"type":"assistant","timestamp":"2026-04-19T10:00:00.000Z","message":{"id":"msg_1","type":"message","role":"assistant","content":[],"model":"claude-sonnet-4-5","stop_reason":"end_turn","usage":{"input_tokens":1000,"output_tokens":500,"cache_read_input_tokens":200,"cache_creation_input_tokens":50}}}
{"type":"assistant","timestamp":"2026-04-19T11:00:00.000Z","message":{"id":"msg_2","type":"message","role":"assistant","content":[],"model":"claude-sonnet-4-5","stop_reason":"end_turn","usage":{"input_tokens":800,"output_tokens":300,"cache_read_input_tokens":100,"cache_creation_input_tokens":0}}}
JSONL

# Create state.yaml fixture with timestamps bracketing the JSONL mtime
CHANGE_ID="test-ingest-driver-auto"
cat > "$STATE_DIR/state.yaml" << YAML
change_id: $CHANGE_ID
repo_root: $FAKE_REPO
schema: feature
status: completed
started_at: "2026-04-19T09:00:00Z"
completed_at: "2026-04-19T12:00:00Z"
YAML

# ── Test 1: script must exist (RED: it doesn't yet) ─────────────────────────
echo "=== Test: ingest-driver-auto step script exists ==="
if [[ -f "$SCRIPT" ]]; then
  pass "step script exists at $SCRIPT"
else
  fail "step script NOT found at $SCRIPT (expected — RED test)"
  echo ""
  echo "Results: $PASS passed, $FAIL failed"
  exit 1
fi

# ── Test 2: primary path — session_id from TMPDIR UUID component ─────────────
echo "=== Test: TMPDIR-based session_id resolution (primary path) ==="
# Simulate the TMPDIR that Claude Code sets during an agent session:
#   /tmp/claude-501/<repo-slug-dir>/<session-uuid>/
FAKE_AGENT_TMPDIR="$FAKE_BASE/tmp/$REPO_SLUG/$SESSION_ID"
mkdir -p "$FAKE_AGENT_TMPDIR"

RESULT=$(HOME="$FAKE_HOME" \
  TMPDIR="$FAKE_AGENT_TMPDIR" \
  METRICS_DB="$METRICS_DB_PATH" \
  ORCHESTRATOR_HOME="$REPO_ROOT" \
  python3 "$SCRIPT" "$STATE_DIR/state.yaml" 2>/dev/null)

EXIT_CODE=$?

if [[ "$EXIT_CODE" -eq 0 ]]; then
  pass "step exits 0 (primary TMPDIR path)"
else
  fail "step exited $EXIT_CODE (expected 0)"
fi

# Check DuckDB for driver-loop row
COUNT=$(HOME="$FAKE_HOME" duckdb "$METRICS_DB_PATH" -csv -noheader \
  "SELECT COUNT(*) FROM step_events WHERE change_id='$CHANGE_ID' AND agent_name='driver-loop';" \
  2>/dev/null || echo "0")

if [[ "$COUNT" -eq 1 ]]; then
  pass "step_events has exactly 1 driver-loop row (primary TMPDIR path)"
else
  fail "step_events has $COUNT driver-loop rows (expected 1 — primary TMPDIR path)"
fi

# Check row has non-null tokens + cost
HAS_TOKENS=$(HOME="$FAKE_HOME" duckdb "$METRICS_DB_PATH" -csv -noheader \
  "SELECT COUNT(*) FROM step_events WHERE change_id='$CHANGE_ID' AND agent_name='driver-loop' AND input_tokens IS NOT NULL AND output_tokens IS NOT NULL AND cost_usd IS NOT NULL;" \
  2>/dev/null || echo "0")

if [[ "$HAS_TOKENS" -eq 1 ]]; then
  pass "driver-loop row has non-null input_tokens, output_tokens, cost_usd"
else
  fail "driver-loop row missing required token/cost fields"
fi

# ── Test 3: fallback path — scan ~/.claude/projects for newest JSONL ──────────
echo "=== Test: fallback scan when TMPDIR has no UUID ==="

# Clear DB for clean state
rm -f "$METRICS_DB_PATH"

RESULT=$(HOME="$FAKE_HOME" \
  TMPDIR="$FAKE_BASE/tmp-no-uuid" \
  METRICS_DB="$METRICS_DB_PATH" \
  ORCHESTRATOR_HOME="$REPO_ROOT" \
  python3 "$SCRIPT" "$STATE_DIR/state.yaml" 2>/dev/null)

EXIT_CODE=$?

if [[ "$EXIT_CODE" -eq 0 ]]; then
  pass "step exits 0 (fallback scan path)"
else
  fail "step exited $EXIT_CODE on fallback path (expected 0)"
fi

COUNT=$(HOME="$FAKE_HOME" duckdb "$METRICS_DB_PATH" -csv -noheader \
  "SELECT COUNT(*) FROM step_events WHERE change_id='$CHANGE_ID' AND agent_name='driver-loop';" \
  2>/dev/null || echo "0")

if [[ "$COUNT" -eq 1 ]]; then
  pass "step_events has exactly 1 driver-loop row (fallback scan path)"
else
  fail "step_events has $COUNT driver-loop rows (expected 1 — fallback scan path)"
fi

# ── Test 4: fail-soft when session_id is unresolvable ───────────────────────
echo "=== Test: fail-soft when no JSONL found ==="

# Use a fake home with no JSONL files
EMPTY_HOME="$FAKE_BASE/emptyhome"
mkdir -p "$EMPTY_HOME"

STDERR_FILE="$FAKE_BASE/test-stderr.txt"
RESULT=$(HOME="$EMPTY_HOME" \
  TMPDIR="$FAKE_BASE/tmp-no-uuid" \
  METRICS_DB="$FAKE_BASE/empty-test.duckdb" \
  ORCHESTRATOR_HOME="$REPO_ROOT" \
  python3 "$SCRIPT" "$STATE_DIR/state.yaml" 2>"$STDERR_FILE")

EXIT_CODE=$?

if [[ "$EXIT_CODE" -eq 0 ]]; then
  pass "step exits 0 on unresolvable session_id (fail-soft)"
else
  fail "step exited $EXIT_CODE on unresolvable session_id (expected 0 — fail-soft)"
fi

RESULT_JSON=$(echo "$RESULT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('skipped','missing'))" 2>/dev/null || echo "")
if [[ "$RESULT_JSON" == "True" ]]; then
  pass "fail-soft output contains skipped=true"
else
  fail "fail-soft output missing skipped=true: got '$RESULT'"
fi

STDERR_CONTENT=$(cat "$STDERR_FILE" 2>/dev/null || echo "")
if echo "$STDERR_CONTENT" | grep -qi "warn\|session.id\|unresolvable\|skip"; then
  pass "stderr contains warning about unresolvable session_id"
else
  fail "stderr missing warning for unresolvable session_id. Got: $STDERR_CONTENT"
fi

# ── Cleanup ──────────────────────────────────────────────────────────────────
rm -rf "$FAKE_BASE"

# ── Summary ─────────────────────────────────────────────────────────────────
echo ""
echo "Results: $PASS passed, $FAIL failed"
if [[ "$FAIL" -gt 0 ]]; then
  exit 1
fi
exit 0
