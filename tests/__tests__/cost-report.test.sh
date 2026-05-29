#!/usr/bin/env bash
# T-8 (RED): Write tests for scripts/cost-report.sh
#
# Test cases:
#   (a) exits 0 and stdout contains all 8 section headers
#   (b) slug-guard rejection returns exit 3
#   (c) unknown change_id returns exit 1 with 'no events' stderr
#   (d) repeated runs byte-identical
#   (e) grep -c '^| Total cost |' == 1 in Exec Summary table
#
# Expected state at T-8: ALL tests FAIL — scripts/cost-report.sh does not exist.
# GREEN fires at T-9 once the script is implemented.
#
# Fixtures:
#   config/scripts/__tests__/fixtures/baseline.duckdb.sql — deterministic DB dump
#   BASELINE_CID = durable-intent-and-resume
#   BASELINE_REPO = /Users/spidey/code/orchestrator

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/cost-report.sh"
FIXTURES_DIR="$(dirname "$0")/fixtures"
BASELINE_SQL="$FIXTURES_DIR/baseline.duckdb.sql"
BASELINE_CID="durable-intent-and-resume"

pass=0
fail=0

check() {
  local desc="$1"
  local result="$2"
  if [[ "$result" -eq 0 ]]; then
    printf "PASS: %s\n" "$desc"
    ((pass++)) || true
  else
    printf "FAIL: %s\n" "$desc"
    ((fail++)) || true
  fi
}

check_contains() {
  local desc="$1"
  local haystack="$2"
  local needle="$3"
  if echo "$haystack" | grep -q "$needle"; then
    printf "PASS: %s\n" "$desc"
    ((pass++)) || true
  else
    printf "FAIL: %s — output does not contain '%s'\n" "$desc" "$needle"
    ((fail++)) || true
  fi
}

# ── Prerequisites ──────────────────────────────────────────────────────────────
if [[ ! -f "$SCRIPT" ]]; then
  echo "FAIL: $SCRIPT does not exist (expected RED at T-8)"
  # Count it as a pre-existing fail so we get the right exit code
  echo "Results: 0 passed, 1 failed"
  exit 1
fi

if [[ ! -f "$BASELINE_SQL" ]]; then
  echo "FAIL: baseline.duckdb.sql not found at $BASELINE_SQL"
  exit 1
fi

if ! command -v duckdb >/dev/null 2>&1; then
  echo "FAIL: duckdb CLI not found on PATH"
  exit 1
fi

# ── Setup temp working area ────────────────────────────────────────────────────
TMPDIR_LOCAL="$(mktemp -d "${TMPDIR:-/tmp}/cost-report-test-XXXXXX")"
trap 'rm -rf "$TMPDIR_LOCAL"' EXIT

DB_PATH="$TMPDIR_LOCAL/baseline.duckdb"

# Load baseline SQL into temp DuckDB
duckdb "$DB_PATH" < "$BASELINE_SQL"
check "baseline DB loaded from SQL dump" $?

export METRICS_DB="$DB_PATH"
export ORCHESTRATOR_HOME="$REPO_ROOT"

# ── (a) Happy path: exits 0, all 8 section headers present ──────────────────
echo ""
echo "--- (a) Happy path: exits 0, all 8 section headers ---"

set +e
OUTPUT=$(bash "$SCRIPT" --change-id "$BASELINE_CID" 2>"$TMPDIR_LOCAL/err_happy.txt")
HAPPY_EXIT=$?
set -e

check "happy path exits 0" $([[ "$HAPPY_EXIT" -eq 0 ]] && echo 0 || echo 1)

if [[ "$HAPPY_EXIT" -ne 0 ]]; then
  echo "  stderr: $(cat "$TMPDIR_LOCAL/err_happy.txt")"
  echo "  stdout: $OUTPUT"
fi

check_contains "header: ## Executive Summary" "$OUTPUT" "^## Executive Summary"
check_contains "header: ## Per-Phase"         "$OUTPUT" "^## Per-Phase"
check_contains "header: ## Per-Agent"         "$OUTPUT" "^## Per-Agent"
check_contains "header: ## Per-Model"         "$OUTPUT" "^## Per-Model"
check_contains "header: ## Native Tools"      "$OUTPUT" "^## Native Tools"
check_contains "header: ## MCP Calls"         "$OUTPUT" "^## MCP Calls"
check_contains "header: ## Per-Agent Tool Use" "$OUTPUT" "^## Per-Agent Tool Use"
check_contains "header: ## Anomalies"         "$OUTPUT" "^## Anomalies"

# ── (b) Slug-guard rejection: exit 3 ─────────────────────────────────────────
echo ""
echo "--- (b) Slug-guard rejection returns exit 3 ---"

set +e
bash "$SCRIPT" --change-id "INVALID_SLUG!" > /dev/null 2>"$TMPDIR_LOCAL/err_slug.txt"
SLUG_EXIT=$?
set -e

check "slug-guard invalid input exits 3" $([[ "$SLUG_EXIT" -eq 3 ]] && echo 0 || echo 1)

set +e
bash "$SCRIPT" --change-id "has spaces" > /dev/null 2>"$TMPDIR_LOCAL/err_slug2.txt"
SLUG2_EXIT=$?
set -e

check "slug-guard (spaces) exits 3" $([[ "$SLUG2_EXIT" -eq 3 ]] && echo 0 || echo 1)

set +e
bash "$SCRIPT" --change-id "-starts-with-dash" > /dev/null 2>"$TMPDIR_LOCAL/err_slug3.txt"
SLUG3_EXIT=$?
set -e

check "slug-guard (leading dash) exits 3" $([[ "$SLUG3_EXIT" -eq 3 ]] && echo 0 || echo 1)

# ── (c) Unknown change_id: exit 1 with 'no events' in stderr ─────────────────
echo ""
echo "--- (c) Unknown change_id returns exit 1 with 'no events' stderr ---"

set +e
UNKNOWN_OUT=$(bash "$SCRIPT" --change-id "nonexistent-change-id" 2>"$TMPDIR_LOCAL/err_unknown.txt")
UNKNOWN_EXIT=$?
UNKNOWN_ERR=$(cat "$TMPDIR_LOCAL/err_unknown.txt")
set -e

check "unknown change_id exits 1" $([[ "$UNKNOWN_EXIT" -eq 1 ]] && echo 0 || echo 1)

if echo "$UNKNOWN_ERR" | grep -qi "no events"; then
  printf "PASS: unknown change_id stderr contains 'no events'\n"
  ((pass++)) || true
else
  printf "FAIL: unknown change_id stderr should contain 'no events', got: '%s'\n" "$UNKNOWN_ERR"
  ((fail++)) || true
fi

# ── (d) Repeated runs byte-identical ─────────────────────────────────────────
echo ""
echo "--- (d) Repeated runs byte-identical ---"

set +e
OUTPUT1=$(bash "$SCRIPT" --change-id "$BASELINE_CID" 2>/dev/null)
EXIT1=$?
OUTPUT2=$(bash "$SCRIPT" --change-id "$BASELINE_CID" 2>/dev/null)
EXIT2=$?
set -e

check "run 1 exits 0" $([[ "$EXIT1" -eq 0 ]] && echo 0 || echo 1)
check "run 2 exits 0" $([[ "$EXIT2" -eq 0 ]] && echo 0 || echo 1)

echo "$OUTPUT1" > "$TMPDIR_LOCAL/run1.txt"
echo "$OUTPUT2" > "$TMPDIR_LOCAL/run2.txt"
DIFF_RUNS=$(diff "$TMPDIR_LOCAL/run1.txt" "$TMPDIR_LOCAL/run2.txt" || true)
check "two runs byte-identical" $([[ -z "$DIFF_RUNS" ]] && echo 0 || echo 1)

if [[ -n "$DIFF_RUNS" ]]; then
  echo "  diff between runs:"
  echo "$DIFF_RUNS" | head -20
fi

# ── (e) Presence check: 'Total cost' row appears exactly once in Exec Summary ─
echo ""
echo "--- (e) '| Total cost |' appears exactly once ---"

TOTAL_COST_COUNT=$(echo "$OUTPUT" | grep -c '^| Total cost |' || true)
if [[ "$TOTAL_COST_COUNT" -eq 1 ]]; then
  printf "PASS: grep -c '^| Total cost |' == 1\n"
  ((pass++)) || true
else
  printf "FAIL: expected 1 match of '^| Total cost |', got %d\n" "$TOTAL_COST_COUNT"
  ((fail++)) || true
fi

echo ""
echo "Results: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
