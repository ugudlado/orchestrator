#!/usr/bin/env bash
# T-9 (RED): Test for ingest-feature-metrics step.
#
# Creates fixture state.yaml + tasks.md in TMPDIR, invokes
# scripts/inline/ingest-feature-metrics.py, then asserts:
#   - One row in feature_metrics for the change_id
#   - tasks_total=5, tasks_completed=4, resolve_rate=0.8
#   - files_changed is non-null (even if 0)
#   - insertions, deletions are non-null
#   - wall_clock_minutes is non-null
#   - review_score_avg is non-null
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/inline/ingest-feature-metrics.py"

pass=0
fail=0

check() {
  local desc="$1"
  local result="$2"   # 0 = pass, nonzero = fail
  if [[ "$result" -eq 0 ]]; then
    echo "PASS: $desc"
    ((pass++))
  else
    echo "FAIL: $desc"
    ((fail++))
  fi
}

check_eq() {
  local desc="$1"
  local got="$2"
  local expected="$3"
  if [[ "$got" == "$expected" ]]; then
    echo "PASS: $desc"
    ((pass++))
  else
    echo "FAIL: $desc — got '$got', expected '$expected'"
    ((fail++))
  fi
}

echo "=== Test: ingest-feature-metrics ==="

# ── Setup TMPDIR fixtures ────────────────────────────────────────────────────
FIXTURE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/test-ingest-XXXXXX")"
trap 'rm -rf "$FIXTURE_DIR"' EXIT

FIXTURE_STATE="$FIXTURE_DIR/state.yaml"
FIXTURE_TASKS="$FIXTURE_DIR/tasks.md"
FIXTURE_DB="$FIXTURE_DIR/metrics.duckdb"

CHANGE_ID="test-ingest-fixture"

# Write fixture tasks.md: 5 tasks, 4 [x] done, 1 [ ] pending
cat > "$FIXTURE_TASKS" <<'YAML'
# Tasks — test-ingest-fixture

- [x] T-1: First task
  Verify: done
- [x] T-2: Second task
  Verify: done
- [x] T-3: Third task
  Verify: done
- [x] T-4: Fourth task
  Verify: done
- [ ] T-5: Fifth task (pending)
  Verify: pending
YAML

# Write fixture state.yaml with:
#   schema=feature, step_history with usage, completed_at, tdd_required=true
cat > "$FIXTURE_STATE" <<YAML
change_id: $CHANGE_ID
slug: $CHANGE_ID
schema: feature
status: completed
repo_root: $REPO_ROOT
worktree_path: $REPO_ROOT
tasks_path: $FIXTURE_TASKS
started_at: "2026-04-10T10:00:00Z"
completed_at: "2026-04-10T10:18:00Z"
flags:
  tdd_required: true
step_history:
  - step_id: execute-next-task
    phase: implement
    status: completed
    agent: developer
    attempt: 1
    started_at: "2026-04-10T10:00:00Z"
    ended_at: "2026-04-10T10:10:00Z"
    usage:
      input_tokens: 5000
      output_tokens: 2000
      total_tokens: 7000
      cost_usd: 0.05
    review_score:
      overall: 8
  - step_id: execute-next-task
    phase: implement
    status: completed
    agent: developer
    attempt: 2
    started_at: "2026-04-10T10:10:00Z"
    ended_at: "2026-04-10T10:15:00Z"
    usage:
      input_tokens: 3000
      output_tokens: 1500
      total_tokens: 4500
      cost_usd: 0.03
    review_score:
      overall: 9
  - step_id: run-phase-review
    phase: implement
    status: completed
    agent: reviewer
    attempt: 1
    started_at: "2026-04-10T10:15:00Z"
    ended_at: "2026-04-10T10:18:00Z"
    usage:
      input_tokens: 2000
      output_tokens: 800
      total_tokens: 2800
      cost_usd: 0.02
    review_score:
      overall: 9
YAML

# ── Script must exist ────────────────────────────────────────────────────────
[[ -f "$SCRIPT" ]]
check "ingest-feature-metrics.py exists" $?

if [[ ! -f "$SCRIPT" ]]; then
  echo ""
  echo "Results: $pass passed, $fail failed"
  [[ "$fail" -eq 0 ]]; exit $?
fi

# ── Run the script ───────────────────────────────────────────────────────────
METRICS_DB="$FIXTURE_DB" \
ORCHESTRATOR_HOME="$REPO_ROOT" \
  python "$SCRIPT" "$FIXTURE_STATE"
check "ingest-feature-metrics.py exits 0" $?

# ── Assert DuckDB row ────────────────────────────────────────────────────────
# Use duckdb CLI to query the temp DB (-csv for parseable output)
QUERY_RESULT=$(duckdb -csv "$FIXTURE_DB" <<SQL
SELECT
  tasks_total,
  tasks_completed,
  resolve_rate,
  files_changed,
  insertions,
  deletions,
  wall_clock_minutes,
  review_score_avg
FROM feature_metrics
WHERE change_id = '$CHANGE_ID'
LIMIT 1;
SQL
)

check "feature_metrics row exists for change_id" $?

# Extract columns (duckdb -csv output: header row + data row, comma-separated)
DATA_ROW=$(echo "$QUERY_RESULT" | tail -1)
TASKS_TOTAL=$(echo "$DATA_ROW" | cut -d',' -f1)
TASKS_DONE=$(echo  "$DATA_ROW" | cut -d',' -f2)
RESOLVE=$(echo     "$DATA_ROW" | cut -d',' -f3)
FILES=$(echo       "$DATA_ROW" | cut -d',' -f4)
INSERTS=$(echo     "$DATA_ROW" | cut -d',' -f5)
DELETES=$(echo     "$DATA_ROW" | cut -d',' -f6)
WALL_CLOCK=$(echo  "$DATA_ROW" | cut -d',' -f7)
SCORE_AVG=$(echo   "$DATA_ROW" | cut -d',' -f8)

check_eq "tasks_total=5"      "$TASKS_TOTAL" "5"
check_eq "tasks_completed=4"  "$TASKS_DONE"  "4"

# resolve_rate should be 0.8 (4/5)
# Check it starts with 0.8
[[ "$RESOLVE" == "0.8"* ]]
check "resolve_rate is 0.8" $?

# files_changed must be non-null (empty string would mean NULL)
[[ -n "$FILES" ]]
check "files_changed is non-null" $?

[[ -n "$INSERTS" ]]
check "insertions is non-null" $?

[[ -n "$DELETES" ]]
check "deletions is non-null" $?

# wall_clock_minutes: started_at=10:00, completed_at=10:18 => 18 minutes
[[ -n "$WALL_CLOCK" ]]
check "wall_clock_minutes is non-null" $?

[[ -n "$SCORE_AVG" ]]
check "review_score_avg is non-null" $?

# ── Fail-loud: missing tasks.md causes non-zero exit ─────────────────────────
FIXTURE_STATE_NOTASKS="$FIXTURE_DIR/state_notasks.yaml"
cat > "$FIXTURE_STATE_NOTASKS" <<YAML
change_id: $CHANGE_ID
slug: $CHANGE_ID
schema: feature
status: completed
repo_root: $REPO_ROOT
worktree_path: $REPO_ROOT
tasks_path: /nonexistent/path/tasks.md
started_at: "2026-04-10T10:00:00Z"
completed_at: "2026-04-10T10:18:00Z"
step_history: []
YAML

METRICS_DB="$FIXTURE_DB" \
ORCHESTRATOR_HOME="$REPO_ROOT" \
  python "$SCRIPT" "$FIXTURE_STATE_NOTASKS" 2>/dev/null
FAIL_EXIT=$?
[[ "$FAIL_EXIT" -ne 0 ]]
check "missing tasks.md causes non-zero exit (fail-loud)" $?

echo ""
echo "Results: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]; exit $?
