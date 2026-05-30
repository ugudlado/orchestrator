#!/usr/bin/env bash
# Tests for config/steps/cost-report (workflow step + metrics implementation).
#
# Metrics (orchestrator_next/scripts/metrics/cost-report.sh):
#   (a) exits 0 and stdout contains all 8 section headers
#   (b) slug-guard rejection returns exit 3
#   (c) unknown change_id returns exit 1 with 'no events' stderr
#   (d) repeated runs byte-identical
#   (e) grep -c '^| Total cost |' == 1 in Exec Summary table
#
# Step (config/steps/cost-report/script.sh):
#   (f) writes cost-summary.md and emits completed JSON with tail_summary
#
# Fixtures: tests/__tests__/fixtures/baseline.duckdb.sql (shared with metrics tests)

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
METRICS_SCRIPT="$REPO_ROOT/orchestrator_next/scripts/metrics/cost-report.sh"
STEP_SCRIPT="$REPO_ROOT/config/steps/cost-report/script.sh"
FIXTURES_DIR="$REPO_ROOT/tests/__tests__/fixtures"
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

if [[ ! -f "$METRICS_SCRIPT" ]]; then
  echo "FAIL: $METRICS_SCRIPT does not exist"
  exit 1
fi

if [[ ! -f "$STEP_SCRIPT" ]]; then
  echo "FAIL: $STEP_SCRIPT does not exist"
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

TMPDIR_LOCAL="$(mktemp -d "${TMPDIR:-/tmp}/cost-report-step-test-XXXXXX")"
trap 'rm -rf "$TMPDIR_LOCAL"' EXIT

DB_PATH="$TMPDIR_LOCAL/baseline.duckdb"
duckdb "$DB_PATH" < "$BASELINE_SQL"
check "baseline DB loaded from SQL dump" $?

export METRICS_DB="$DB_PATH"
export ORCHESTRATOR_HOME="$REPO_ROOT"

# ── (a) Happy path: exits 0, all 8 section headers present ──────────────────
echo ""
echo "--- (a) Metrics: happy path, 8 section headers ---"

set +e
OUTPUT=$(bash "$METRICS_SCRIPT" --change-id "$BASELINE_CID" 2>"$TMPDIR_LOCAL/err_happy.txt")
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
echo "--- (b) Metrics: slug-guard returns exit 3 ---"

set +e
bash "$METRICS_SCRIPT" --change-id "INVALID_SLUG!" > /dev/null 2>/dev/null
SLUG_EXIT=$?
bash "$METRICS_SCRIPT" --change-id "has spaces" > /dev/null 2>/dev/null
SLUG2_EXIT=$?
bash "$METRICS_SCRIPT" --change-id "-starts-with-dash" > /dev/null 2>/dev/null
SLUG3_EXIT=$?
set -e

check "slug-guard invalid input exits 3" $([[ "$SLUG_EXIT" -eq 3 ]] && echo 0 || echo 1)
check "slug-guard (spaces) exits 3" $([[ "$SLUG2_EXIT" -eq 3 ]] && echo 0 || echo 1)
check "slug-guard (leading dash) exits 3" $([[ "$SLUG3_EXIT" -eq 3 ]] && echo 0 || echo 1)

# ── (c) Unknown change_id: exit 1 with 'no events' in stderr ─────────────────
echo ""
echo "--- (c) Metrics: unknown change_id ---"

set +e
bash "$METRICS_SCRIPT" --change-id "nonexistent-change-id" 2>"$TMPDIR_LOCAL/err_unknown.txt"
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
echo "--- (d) Metrics: repeated runs byte-identical ---"

set +e
OUTPUT1=$(bash "$METRICS_SCRIPT" --change-id "$BASELINE_CID" 2>/dev/null)
EXIT1=$?
OUTPUT2=$(bash "$METRICS_SCRIPT" --change-id "$BASELINE_CID" 2>/dev/null)
EXIT2=$?
set -e

check "run 1 exits 0" $([[ "$EXIT1" -eq 0 ]] && echo 0 || echo 1)
check "run 2 exits 0" $([[ "$EXIT2" -eq 0 ]] && echo 0 || echo 1)

echo "$OUTPUT1" > "$TMPDIR_LOCAL/run1.txt"
echo "$OUTPUT2" > "$TMPDIR_LOCAL/run2.txt"
DIFF_RUNS=$(diff "$TMPDIR_LOCAL/run1.txt" "$TMPDIR_LOCAL/run2.txt" || true)
check "two runs byte-identical" $([[ -z "$DIFF_RUNS" ]] && echo 0 || echo 1)

# ── (e) '| Total cost |' appears exactly once ────────────────────────────────
echo ""
echo "--- (e) Metrics: Total cost row count ---"

TOTAL_COST_COUNT=$(echo "$OUTPUT" | grep -c '^| Total cost |' || true)
if [[ "$TOTAL_COST_COUNT" -eq 1 ]]; then
  printf "PASS: grep -c '^| Total cost |' == 1\n"
  ((pass++)) || true
else
  printf "FAIL: expected 1 match of '^| Total cost |', got %d\n" "$TOTAL_COST_COUNT"
  ((fail++)) || true
fi

# ── (f) Step script writes cost-summary.md and JSON outputs ──────────────────
echo ""
echo "--- (f) Step: cost-summary.md + completion JSON ---"

CHANGE_DIR="$TMPDIR_LOCAL/spec/changes/$BASELINE_CID"
mkdir -p "$CHANGE_DIR"
STATE_YAML="$CHANGE_DIR/state.yaml"
cat > "$STATE_YAML" <<YAML
change_id: $BASELINE_CID
repo_root: $REPO_ROOT
status: active
YAML

set +e
STEP_OUT=$(ORCHESTRATOR_STATE_YAML_PATH="$STATE_YAML" \
  ORCHESTRATOR_REPO_ROOT="$REPO_ROOT" \
  ORCHESTRATOR_HOME="$REPO_ROOT" \
  METRICS_DB="$DB_PATH" \
  bash "$STEP_SCRIPT" 2>"$TMPDIR_LOCAL/err_step.txt")
STEP_EXIT=$?
set -e

check "step script exits 0" $([[ "$STEP_EXIT" -eq 0 ]] && echo 0 || echo 1)
check "cost-summary.md written" $([[ -f "$CHANGE_DIR/cost-summary.md" ]] && echo 0 || echo 1)
check "cost-summary.md non-empty" $([[ -s "$CHANGE_DIR/cost-summary.md" ]] && echo 0 || echo 1)

if echo "$STEP_OUT" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert d.get('status') == 'completed', d
outs = d.get('outputs') or {}
assert outs.get('tail_summary'), 'missing tail_summary'
assert outs.get('cost_summary_path'), 'missing cost_summary_path'
" 2>/dev/null; then
  printf "PASS: step JSON status completed with tail_summary and cost_summary_path\n"
  ((pass++)) || true
else
  printf "FAIL: step JSON invalid or incomplete: %s\n" "$STEP_OUT"
  cat "$TMPDIR_LOCAL/err_step.txt" >&2 2>/dev/null || true
  ((fail++)) || true
fi

check_contains "cost-summary.md has Executive Summary" "$(cat "$CHANGE_DIR/cost-summary.md" 2>/dev/null)" "^## Executive Summary"

echo ""
echo "Results: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
