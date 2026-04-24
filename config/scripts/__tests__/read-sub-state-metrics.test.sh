#!/usr/bin/env bash
# T-6 (parity contract) / T-7 (GREEN): byte-equivalence test for read-sub-state-metrics.sh rewrite.
#
# Parity contract:
#   1. Load baseline.duckdb.sql into a temp DB.
#   2. Run config/scripts/read-sub-state-metrics.sh with SLUG=durable-intent-and-resume.
#   3. Compare raw stdout against committed baseline fixture — diff must be empty.
#   4. Assert exactly three top-level keys under metrics: (tokens.total, duration_ms,
#      churn.files_changed) and no extraneous keys.
#
# RED timing note: This test is a parity-contract test, not a traditional failing-first
# RED. The current pre-rewrite read-sub-state-metrics.sh shells out to
# `orchestrator metrics --change-id $SLUG`. That CLI still works pre-T-8, so this test
# PASSES against the old script (green from the start). The true RED fires at T-8 when
# the `metrics` verb is retired from bin/orchestrator — at that point, any code that
# calls `orchestrator metrics` would break this test. The rewrite (T-7) eliminates the
# orchestrator dependency, making it robust past T-8.
#
# Fixtures:
#   config/scripts/__tests__/fixtures/baseline.duckdb.sql — deterministic DB dump
#   config/scripts/__tests__/fixtures/baseline_read_sub_state_metrics.yaml — frozen bytes

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
SCRIPT="$REPO_ROOT/config/scripts/read-sub-state-metrics.sh"
FIXTURES_DIR="$(dirname "$0")/fixtures"
BASELINE_SQL="$FIXTURES_DIR/baseline.duckdb.sql"
BASELINE_FIXTURE="$FIXTURES_DIR/baseline_read_sub_state_metrics.yaml"
BASELINE_SLUG="durable-intent-and-resume"

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

check_absent() {
  local desc="$1"
  local haystack="$2"
  local needle="$3"
  if echo "$haystack" | grep -qE "$needle"; then
    printf "FAIL: %s — output should NOT contain '%s'\n" "$desc" "$needle"
    ((fail++)) || true
  else
    printf "PASS: %s\n" "$desc"
    ((pass++)) || true
  fi
}

# ── Prerequisites ─────────────────────────────────────────────────────────────
if [[ ! -f "$SCRIPT" ]]; then
  echo "FAIL: $SCRIPT does not exist"
  exit 1
fi
if [[ ! -f "$BASELINE_SQL" ]]; then
  echo "FAIL: baseline.duckdb.sql not found at $BASELINE_SQL"
  exit 1
fi
if [[ ! -f "$BASELINE_FIXTURE" ]]; then
  echo "FAIL: baseline_read_sub_state_metrics.yaml not found at $BASELINE_FIXTURE"
  exit 1
fi
if ! command -v duckdb >/dev/null 2>&1; then
  echo "FAIL: duckdb CLI not found on PATH"
  exit 1
fi

# ── Setup temp working area ───────────────────────────────────────────────────
TMPDIR_LOCAL="$(mktemp -d "${TMPDIR:-/tmp}/rsm-parity-test-XXXXXX")"
trap 'rm -rf "$TMPDIR_LOCAL"' EXIT

DB_PATH="$TMPDIR_LOCAL/baseline.duckdb"

# Load baseline SQL into temp DuckDB
duckdb "$DB_PATH" < "$BASELINE_SQL"
check "baseline DB loaded from SQL dump" $?

export METRICS_DB="$DB_PATH"
export ORCHESTRATOR_HOME="$REPO_ROOT"

# ── Run the script ────────────────────────────────────────────────────────────
set +e
OUTPUT=$(bash "$SCRIPT" "$BASELINE_SLUG" 2>"$TMPDIR_LOCAL/err.txt")
SCRIPT_EXIT=$?
set -e

check "script exits 0" $([[ "$SCRIPT_EXIT" -eq 0 ]] && echo 0 || echo 1)

if [[ "$SCRIPT_EXIT" -ne 0 ]]; then
  echo "  stderr: $(cat "$TMPDIR_LOCAL/err.txt")"
  echo "  stdout: $OUTPUT"
  echo "Results: $pass passed, $fail failed"
  exit 1
fi

# ── Diff against committed baseline fixture ───────────────────────────────────
echo "$OUTPUT" > "$TMPDIR_LOCAL/run.txt"
DIFF_FIXTURE=$(diff "$TMPDIR_LOCAL/run.txt" "$BASELINE_FIXTURE" || true)
check "output matches baseline fixture (byte-identical)" \
  $([[ -z "$DIFF_FIXTURE" ]] && echo 0 || echo 1)

if [[ -n "$DIFF_FIXTURE" ]]; then
  echo "  diff (output vs fixture):"
  echo "$DIFF_FIXTURE" | head -20
fi

# ── Assert narrow contract: exactly three top-level keys ─────────────────────
echo ""
echo "--- narrow contract: exactly three required keys under metrics: ---"
check_contains "metrics: top-level key present"   "$OUTPUT" "^metrics:"
check_contains "tokens.total is present"          "$OUTPUT" "    total:"
check_contains "duration_ms is present"           "$OUTPUT" "  duration_ms:"
check_contains "churn.files_changed is present"   "$OUTPUT" "    files_changed:"

# Assert tokens.total is a positive integer (843804 from baseline)
TOK_VAL=$(echo "$OUTPUT" | grep "total:" | awk '{print $2}' | head -1)
if [[ -n "$TOK_VAL" && "$TOK_VAL" -gt 0 ]] 2>/dev/null; then
  check "tokens.total is a positive integer from baseline DB" 0
else
  check "tokens.total is a positive integer from baseline DB — got '$TOK_VAL'" 1
fi

# Assert duration_ms is a positive integer (9615807 from baseline)
DUR_VAL=$(echo "$OUTPUT" | grep "duration_ms:" | awk '{print $2}' | head -1)
if [[ -n "$DUR_VAL" && "$DUR_VAL" -gt 0 ]] 2>/dev/null; then
  check "duration_ms is a positive integer from baseline DB" 0
else
  check "duration_ms is a positive integer from baseline DB — got '$DUR_VAL'" 1
fi

echo ""
echo "--- narrow contract: no extraneous keys ---"
check_absent "cost: block is absent (narrow contract)"            "$OUTPUT" "^  cost:"
check_absent "resolution: block is absent (narrow contract)"      "$OUTPUT" "^  resolution:"
check_absent "benchmarks: block is absent (narrow contract)"      "$OUTPUT" "^  benchmarks:"
check_absent "per_agent_tokens absent (narrow contract)"          "$OUTPUT" "per_agent_tokens:"
check_absent "tokens.input absent (only total allowed)"           "$OUTPUT" "    input:"
check_absent "tokens.output absent (only total allowed)"          "$OUTPUT" "    output:"

echo ""
echo "Results: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
