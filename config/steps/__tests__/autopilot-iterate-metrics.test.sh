#!/usr/bin/env bash
# Test: autopilot per-iteration metrics extraction helper
# Tests: scripts/read-sub-state-metrics.sh
#
# Asserts:
#   1. Active path: reads ~/.workflows/<slug>/state.yaml, sums step_history usage,
#      produces iteration metrics block with correct tokens.total
#   2. Archive fallback: when active path absent, reads spec/changes/archive/<slug>/state.yaml
#   3. Missing both paths: helper exits non-zero with an error message
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
HELPER="$REPO_ROOT/config/scripts/read-sub-state-metrics.sh"

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

# Helper must exist
if [[ ! -f "$HELPER" ]]; then
  echo "FAIL: $HELPER does not exist"
  exit 1
fi

TMPDIR_BASE="${TMPDIR:-/tmp}/hl278-autopilot-test-$$"
mkdir -p "$TMPDIR_BASE"
cleanup() { rm -rf "$TMPDIR_BASE"; }
trap cleanup EXIT

# ── Fixture: sub-feature state.yaml with known usage ─────────────────────
# total_tokens per step: 5000 + 3000 = 8000
# cost total: computed from tokens
# duration_ms: 30000 + 20000 = 50000
SLUG="hl-test-sub-feature-001"

create_sub_state() {
  local path="$1"
  mkdir -p "$(dirname "$path")"
  cat >"$path" <<'STATEYAML'
change_id: hl-test-sub-feature-001
slug: hl-test-sub-feature-001
schema: feature
status: completed
started_at: "2026-04-10T10:00:00Z"
completed_at: "2026-04-10T11:00:00Z"
step_history:
  - step_id: execute-next-task
    phase: implement
    status: completed
    agent: developer
    started_at: "2026-04-10T10:00:00Z"
    completed_at: "2026-04-10T10:30:00Z"
    usage:
      total_tokens: 5000
      tool_uses: 10
      duration_ms: 30000
  - step_id: execute-next-task
    phase: implement
    status: completed
    agent: developer
    started_at: "2026-04-10T10:30:00Z"
    completed_at: "2026-04-10T11:00:00Z"
    usage:
      total_tokens: 3000
      tool_uses: 8
      duration_ms: 20000
metrics:
  churn:
    files_changed: 5
    insertions: 100
    deletions: 20
    total_commits: 3
STATEYAML
}

# ── Test 1: Active path (HOME/.workflows/<slug>/state.yaml) ──────────────
echo "--- Test 1: active path ---"
ACTIVE_STATE="$TMPDIR_BASE/.workflows/$SLUG/state.yaml"
create_sub_state "$ACTIVE_STATE"

# Run helper with HOME overridden to tmpdir and REPO_ROOT pointing to tmpdir
OUT=$(HOME="$TMPDIR_BASE" REPO_ROOT="$TMPDIR_BASE/repo" bash "$HELPER" "$SLUG" 2>/dev/null)
EXIT_CODE=$?

check "helper exits 0 for active path" "$EXIT_CODE" "0"
check_contains "active: metrics: key present" "$OUT" "metrics:"
check_contains "active: tokens.total = 8000" "$OUT" "total: 8000"
check_contains "active: duration_ms = 50000" "$OUT" "duration_ms: 50000"
check_contains "active: churn.files_changed present" "$OUT" "files_changed:"

# ── Test 2: Archive fallback (spec/changes/archive/<slug>/state.yaml) ─────
echo "--- Test 2: archive fallback ---"
ARCHIVE_STATE="$TMPDIR_BASE/repo/spec/changes/archive/$SLUG/state.yaml"
create_sub_state "$ARCHIVE_STATE"

# Active path does NOT exist for this test
OUT_ARCHIVE=$(HOME="$TMPDIR_BASE/nohome" REPO_ROOT="$TMPDIR_BASE/repo" bash "$HELPER" "$SLUG" 2>/dev/null)
EXIT_ARCHIVE=$?

check "helper exits 0 for archive fallback" "$EXIT_ARCHIVE" "0"
check_contains "archive: tokens.total = 8000" "$OUT_ARCHIVE" "total: 8000"

# ── Test 3: Neither path exists — helper exits non-zero ───────────────────
echo "--- Test 3: missing state.yaml ---"
OUT_MISSING=$(HOME="$TMPDIR_BASE/nohome2" REPO_ROOT="$TMPDIR_BASE/norepo" bash "$HELPER" "nonexistent-slug" 2>&1 || true)
EXIT_MISSING=$?

if [[ "$EXIT_MISSING" -ne 0 ]]; then
  echo "PASS: helper exits non-zero when state.yaml missing"
  ((pass++))
else
  echo "FAIL: helper should exit non-zero when state.yaml missing (got exit $EXIT_MISSING)"
  ((fail++))
fi

echo ""
echo "Results: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
