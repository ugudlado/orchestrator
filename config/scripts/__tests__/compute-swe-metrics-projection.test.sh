#!/usr/bin/env bash
# T-4/T-5: byte-equivalence test for compute-swe-metrics.sh rewrite.
#
# Parity contract:
#   1. Load baseline.duckdb.sql into a temp DB.
#   2. Run scripts/inline/compute-swe-metrics.sh with a temp state.yaml
#      pointing to the baseline change_id (durable-intent-and-resume).
#   3. Compare raw output against committed baseline fixture. Diff must be empty.
#   4. Run twice for determinism (UC-E3): two runs byte-identical.
#
# COMPUTE_SWE_SOURCE_TS=BASELINE is exported before each run so that the
# source: field in the output is "duckdb@BASELINE" — matching the fixture exactly.
# This enables a raw byte-identical diff with no stripping required (AC-5).

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/inline/compute-swe-metrics.sh"
FIXTURES_DIR="$(dirname "$0")/fixtures"
BASELINE_SQL="$FIXTURES_DIR/baseline.duckdb.sql"
BASELINE_FIXTURE="$FIXTURES_DIR/baseline_compute_swe_metrics.yaml"
BASELINE_CHANGE_ID="durable-intent-and-resume"
BASELINE_REPO_ROOT="/Users/spidey/code/orchestrator"

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

# Prerequisites
if [[ ! -f "$SCRIPT" ]]; then
  echo "FAIL: $SCRIPT does not exist"
  exit 1
fi
if [[ ! -f "$BASELINE_SQL" ]]; then
  echo "FAIL: baseline.duckdb.sql not found at $BASELINE_SQL"
  exit 1
fi
if [[ ! -f "$BASELINE_FIXTURE" ]]; then
  echo "FAIL: baseline_compute_swe_metrics.yaml not found at $BASELINE_FIXTURE"
  exit 1
fi
if ! command -v duckdb >/dev/null 2>&1; then
  echo "FAIL: duckdb CLI not found on PATH"
  exit 1
fi

# Setup temp area
TMPDIR_LOCAL="$(mktemp -d "${TMPDIR:-/tmp}/csm-parity-test-XXXXXX")"
trap 'rm -rf "$TMPDIR_LOCAL"' EXIT

DB_PATH="$TMPDIR_LOCAL/baseline.duckdb"

# Load baseline SQL into temp DuckDB
duckdb "$DB_PATH" < "$BASELINE_SQL"
check "baseline DB loaded from SQL dump" $?

# Create temp state.yaml pointing to baseline change_id
STATE_DIR="$TMPDIR_LOCAL/state"
mkdir -p "$STATE_DIR"
cat > "$STATE_DIR/state.yaml" <<YAML
change_id: $BASELINE_CHANGE_ID
slug: $BASELINE_CHANGE_ID
schema: feature
status: completed
repo_root: $BASELINE_REPO_ROOT
started_at: "2026-04-21T05:46:00Z"
completed_at: "2026-04-21T11:35:16Z"
YAML

export METRICS_DB="$DB_PATH"
export ORCHESTRATOR_HOME="$REPO_ROOT"
# Freeze timestamp so source: field is deterministic across runs and matches fixture
export COMPUTE_SWE_SOURCE_TS=BASELINE

# ── Run 1 ─────────────────────────────────────────────────────────────────────
set +e
OUTPUT1=$(bash "$SCRIPT" "$STATE_DIR" 2>"$TMPDIR_LOCAL/err1.txt")
EXIT1=$?
set -e

check "run 1 exits 0" $([[ "$EXIT1" -eq 0 ]] && echo 0 || echo 1)

if [[ "$EXIT1" -ne 0 ]]; then
  echo "  stderr: $(cat "$TMPDIR_LOCAL/err1.txt")"
  echo "Results: $pass passed, $fail failed"
  exit 1
fi

# ── Run 2 (determinism check — UC-E3) ────────────────────────────────────────
set +e
OUTPUT2=$(bash "$SCRIPT" "$STATE_DIR" 2>"$TMPDIR_LOCAL/err2.txt")
EXIT2=$?
set -e

check "run 2 exits 0" $([[ "$EXIT2" -eq 0 ]] && echo 0 || echo 1)

# Two runs byte-identical — use temp files, no /dev/fd substitution
echo "$OUTPUT1" > "$TMPDIR_LOCAL/run1.txt"
echo "$OUTPUT2" > "$TMPDIR_LOCAL/run2.txt"
DIFF_RUNS=$(diff "$TMPDIR_LOCAL/run1.txt" "$TMPDIR_LOCAL/run2.txt" || true)
check "two successive runs byte-identical" \
  $([[ -z "$DIFF_RUNS" ]] && echo 0 || echo 1)

if [[ -n "$DIFF_RUNS" ]]; then
  echo "  diff between run1 and run2:"
  echo "$DIFF_RUNS" | head -20
fi

# ── Diff against committed baseline fixture ───────────────────────────────────
DIFF_FIXTURE=$(diff "$TMPDIR_LOCAL/run1.txt" "$BASELINE_FIXTURE" || true)
check "output matches baseline fixture (byte-identical)" \
  $([[ -z "$DIFF_FIXTURE" ]] && echo 0 || echo 1)

if [[ -n "$DIFF_FIXTURE" ]]; then
  echo "  diff (output vs fixture):"
  echo "$DIFF_FIXTURE" | head -40
fi

# ── Structural sanity: output has metrics: top-level key ─────────────────────
if echo "$OUTPUT1" | grep -q "^metrics:"; then
  printf "PASS: output has top-level metrics: key\n"
  ((pass++)) || true
else
  printf "FAIL: output is missing top-level metrics: key\n"
  ((fail++)) || true
fi

echo ""
echo "Results: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
