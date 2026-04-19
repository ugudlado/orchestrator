#!/usr/bin/env bash
# T-19: End-to-end integration test — seeded DuckDB + fresh ingest + read-back.
#
# This test exercises the FULL new pipeline without bypassing any layer:
#   1. Seed step_events with a realistic feature's worth of rows.
#   2. Create fixture state.yaml + tasks.md in TMPDIR.
#   3. Invoke scripts/inline/ingest-feature-metrics.py → writes feature_metrics row.
#   4. Invoke 'orchestrator metrics --change-id X --format json' → read-back.
#   5. Assert every field marked REQUIRED for schema=feature in metrics-schema.md
#      is present in the JSON output (null = failure, 0 = pass).
#
# IMPORTANT: This test does NOT call upsert_feature_metrics() directly.
# All feature_metrics data must flow through the real ingest script.
# If a required field is null, the test FAILS and the finding is reported.
# This is exactly the class of bug the task's STOP-and-report constraint catches.
#
# repo_root consistency: both step_events (seeded here) and feature_metrics (written
# by ingest) use the same repo_root = REPO_ROOT (worktree directory). The orchestrator
# metrics subcommand resolves repo_root from step_events, so they must match.
#
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CHANGE_ID="integration-test-abc"
INGEST_SCRIPT="$REPO_ROOT/scripts/inline/ingest-feature-metrics.py"

pass=0
fail=0
findings=()

check() {
  local desc="$1"
  local result="$2"  # 0 = pass, nonzero = fail
  if [[ "$result" -eq 0 ]]; then
    printf "PASS: %s\n" "$desc"
    ((pass++)) || true
  else
    printf "FAIL: %s\n" "$desc"
    ((fail++)) || true
  fi
}

# Like check but records scope-mismatch findings separately for reporting
check_finding() {
  local desc="$1"
  local result="$2"
  local note="${3:-}"
  if [[ "$result" -eq 0 ]]; then
    printf "PASS: %s\n" "$desc"
    ((pass++)) || true
  else
    printf "FINDING: %s\n" "$desc"
    if [[ -n "$note" ]]; then
      printf "         %s\n" "$note"
    fi
    findings+=("$desc")
    ((fail++)) || true
  fi
}

echo "=== Test: metrics pipeline integration (T-19) ==="
echo "    REPO_ROOT: $REPO_ROOT"
echo "    CHANGE_ID: $CHANGE_ID"
echo ""

# ── Prerequisites ────────────────────────────────────────────────────────────
command -v jq >/dev/null 2>&1
check "jq is available" $?

[[ -f "$INGEST_SCRIPT" ]]
check "ingest-feature-metrics.py exists" $?

[[ -f "$REPO_ROOT/bin/orchestrator" ]]
check "bin/orchestrator exists" $?

# ── Setup temp environment ────────────────────────────────────────────────────
TMPDIR_LOCAL="$(mktemp -d "${TMPDIR:-/tmp}/metrics-integration-XXXXXX")"
trap 'rm -rf "$TMPDIR_LOCAL"' EXIT

export METRICS_DB="$TMPDIR_LOCAL/metrics.duckdb"
export PYTHONPATH="$REPO_ROOT/config/scripts"
export ORCHESTRATOR_HOME="$REPO_ROOT"

FIXTURE_STATE="$TMPDIR_LOCAL/state.yaml"
FIXTURE_TASKS="$TMPDIR_LOCAL/tasks.md"

# ── Write fixture tasks.md ────────────────────────────────────────────────────
# 20 tasks: 18 [x] completed, 1 [~] skipped, 1 [ ] pending
cat > "$FIXTURE_TASKS" <<'YAML'
# Tasks — integration-test-abc

- [x] T-1: Bootstrap DuckDB schema
  Verify: schema created
- [x] T-2: Add turns column migration
  Verify: column present
- [x] T-3: Write failing test for turns
  Verify: red
- [x] T-4: Green turns test
  Verify: green
- [x] T-5: Write failing test for _totals() widening
  Verify: red
- [x] T-6: Implement _totals() cache/turns/gross_usd
  Verify: green
- [x] T-7: Write failing test for feature_metrics DDL
  Verify: red
- [x] T-8: Implement feature_metrics table
  Verify: green
- [x] T-9: Write failing test for metrics subcommand
  Verify: red
- [x] T-10: Implement orchestrator metrics
  Verify: green
- [x] T-11: Write failing test for ingest step
  Verify: red
- [x] T-12: Implement ingest-feature-metrics
  Verify: green
- [x] T-13: Write complete-phase order test
  Verify: red
- [x] T-14: Wire ingest into complete-phase
  Verify: green
- [x] T-15: Write byte-compat test
  Verify: red
- [x] T-16: Rewrite compute-swe-metrics.sh
  Verify: green
- [x] T-17: Write narrow contract test
  Verify: red
- [x] T-18: Rewrite read-sub-state-metrics.sh
  Verify: green
- [~] T-19: Optional enhancement (skipped)
  Verify: n/a
- [ ] T-20: Phase gate (pending)
  Verify: all pass
YAML

# ── Write fixture state.yaml ──────────────────────────────────────────────────
# worktree_path points to $TMPDIR_LOCAL (not a git repo) so run_git_churn()
# returns zeros deterministically (non-fatal fall-through).
# repo_root = $REPO_ROOT must match what we'll use when seeding step_events.
cat > "$FIXTURE_STATE" <<YAML
change_id: $CHANGE_ID
slug: $CHANGE_ID
schema: feature
status: completed
repo_root: $REPO_ROOT
worktree_path: $TMPDIR_LOCAL
tasks_path: $FIXTURE_TASKS
started_at: "2026-04-20T08:00:00Z"
completed_at: "2026-04-20T10:30:00Z"
flags:
  tdd_required: true
retries:
  execute-next-task: 2
  run-phase-review: 0
human_interventions: 0
step_history:
  - step_id: explore
    phase: specify
    status: completed
    agent: discoverer
    attempt: 1
    started_at: "2026-04-20T08:00:00Z"
    ended_at: "2026-04-20T08:15:00Z"
    usage:
      input_tokens: 20000
      output_tokens: 4000
      cache_creation_input_tokens: 8000
      cache_read_input_tokens: 15000
      total_tokens: 47000
      cost_usd: 0.15
      duration_ms: 900000
      model: claude-sonnet-4-6
    review_score:
      overall: 8
  - step_id: execute-next-task
    phase: implement
    status: completed
    agent: developer
    attempt: 1
    started_at: "2026-04-20T08:15:00Z"
    ended_at: "2026-04-20T09:00:00Z"
    usage:
      input_tokens: 30000
      output_tokens: 6000
      cache_creation_input_tokens: 12000
      cache_read_input_tokens: 22000
      total_tokens: 70000
      cost_usd: 0.28
      duration_ms: 2700000
      model: claude-sonnet-4-6
  - step_id: execute-next-task
    phase: implement
    status: completed
    agent: developer
    attempt: 2
    started_at: "2026-04-20T09:00:00Z"
    ended_at: "2026-04-20T09:45:00Z"
    usage:
      input_tokens: 25000
      output_tokens: 5000
      cache_creation_input_tokens: 10000
      cache_read_input_tokens: 20000
      total_tokens: 60000
      cost_usd: 0.22
      duration_ms: 2700000
      model: claude-sonnet-4-6
  - step_id: run-phase-review
    phase: implement
    status: completed
    agent: reviewer
    attempt: 1
    started_at: "2026-04-20T09:45:00Z"
    ended_at: "2026-04-20T10:00:00Z"
    usage:
      input_tokens: 8000
      output_tokens: 2000
      cache_creation_input_tokens: 3000
      cache_read_input_tokens: 6000
      total_tokens: 19000
      cost_usd: 0.08
      duration_ms: 900000
      model: claude-sonnet-4-6
    review_score:
      overall: 9
YAML

echo "--- Step 1: Seed step_events (repo_root=$REPO_ROOT) ---"

# Seed step_events using REPO_ROOT as repo_root.
# This MUST match the repo_root in fixture state.yaml above.
# The orchestrator metrics subcommand resolves repo_root from step_events,
# so feature_metrics (written by ingest using state["repo_root"]) will match.
python3 - <<PY
import os, sys, json
sys.path.insert(0, os.environ["PYTHONPATH"])
from orchestrator_next.upsert import ensure_schema, upsert_synthetic_event
import duckdb

METRICS_DB = os.environ["METRICS_DB"]
REPO_ROOT   = os.environ["ORCHESTRATOR_HOME"]
CHANGE_ID   = "$CHANGE_ID"

db = duckdb.connect(METRICS_DB)
ensure_schema(db)

steps = [
    dict(
        agent_name="discoverer", step_id="explore", phase="specify",
        usage=dict(
            model="claude-sonnet-4-6",
            input_tokens=20000, output_tokens=4000,
            cache_read_input_tokens=15000, cache_creation_input_tokens=8000,
            cost_usd=0.15, duration_ms=900000, turns=10,
            tool_calls={"Read": 20, "Grep": 8, "Bash": 5},
        ),
        started_at="2026-04-20T08:00:00", ended_at="2026-04-20T08:15:00",
    ),
    dict(
        agent_name="developer", step_id="execute-next-task", phase="implement",
        usage=dict(
            model="claude-sonnet-4-6",
            input_tokens=30000, output_tokens=6000,
            cache_read_input_tokens=22000, cache_creation_input_tokens=12000,
            cost_usd=0.28, duration_ms=2700000, turns=18,
            tool_calls={"Read": 45, "Edit": 15, "Write": 8, "Bash": 12},
        ),
        started_at="2026-04-20T08:15:00", ended_at="2026-04-20T09:00:00",
    ),
    dict(
        agent_name="developer", step_id="execute-next-task", phase="implement",
        usage=dict(
            model="claude-sonnet-4-6",
            input_tokens=25000, output_tokens=5000,
            cache_read_input_tokens=20000, cache_creation_input_tokens=10000,
            cost_usd=0.22, duration_ms=2700000, turns=15,
            tool_calls={"Read": 35, "Edit": 12, "Write": 6, "Bash": 8},
        ),
        started_at="2026-04-20T09:00:00", ended_at="2026-04-20T09:45:00",
    ),
    dict(
        agent_name="reviewer", step_id="run-phase-review", phase="implement",
        usage=dict(
            model="claude-sonnet-4-6",
            input_tokens=8000, output_tokens=2000,
            cache_read_input_tokens=6000, cache_creation_input_tokens=3000,
            cost_usd=0.08, duration_ms=900000, turns=5,
            tool_calls={"Read": 12, "Grep": 5},
        ),
        started_at="2026-04-20T09:45:00", ended_at="2026-04-20T10:00:00",
    ),
]

for step in steps:
    upsert_synthetic_event(
        db,
        {"repo_root": REPO_ROOT, "change_id": CHANGE_ID},
        agent_name=step["agent_name"],
        step_id=step["step_id"],
        phase=step["phase"],
        usage=step["usage"],
        started_at=step["started_at"],
        ended_at=step["ended_at"],
    )

db.close()
print("step_events seed OK — 4 rows, repo_root=" + REPO_ROOT)
PY

check "step_events seeded without error" $?

echo ""
echo "--- Step 2: Run ingest-feature-metrics.py ---"
echo "    (writes feature_metrics using repo_root from state.yaml)"

python3 "$INGEST_SCRIPT" "$FIXTURE_STATE" \
  2>"$TMPDIR_LOCAL/ingest_err.txt"
INGEST_EXIT=$?

check "ingest-feature-metrics.py exits 0" $INGEST_EXIT

if [[ $INGEST_EXIT -ne 0 ]]; then
  echo "ingest stderr: $(cat "$TMPDIR_LOCAL/ingest_err.txt")"
  echo ""
  echo "Results: $pass passed, $fail failed"
  exit 1
fi

# Verify feature_metrics row was written
ROW_COUNT=$(duckdb -csv "$METRICS_DB" \
  "SELECT COUNT(*) FROM feature_metrics WHERE change_id = '$CHANGE_ID'" \
  2>/dev/null | tail -1)
check "feature_metrics row written by ingest" $([[ "$ROW_COUNT" == "1" ]] && echo 0 || echo 1)

echo ""
echo "--- Step 3: Invoke 'orchestrator metrics --change-id $CHANGE_ID --format json' ---"

"$REPO_ROOT/bin/orchestrator" metrics \
  --change-id "$CHANGE_ID" \
  --format json \
  >"$TMPDIR_LOCAL/out.json" 2>"$TMPDIR_LOCAL/err.txt"
CLI_EXIT=$?

check "'orchestrator metrics' exits 0" $([[ "$CLI_EXIT" -eq 0 ]] && echo 0 || echo 1)

if [[ "$CLI_EXIT" -ne 0 ]]; then
  echo "stderr: $(cat "$TMPDIR_LOCAL/err.txt")"
  echo ""
  echo "Results: $pass passed, $fail failed"
  exit 1
fi

echo ""
echo "--- Step 4: Assert JSON fields (all REQUIRED for schema=feature) ---"

# Helper: assert a jq path is present and non-null
assert_field() {
  local path="$1"
  local desc="$2"
  local note="${3:-}"
  local val
  val=$(jq -r "$path // \"__MISSING__\"" "$TMPDIR_LOCAL/out.json" 2>/dev/null)
  if [[ "$val" != "__MISSING__" && "$val" != "null" && "$val" != "" ]]; then
    printf "PASS: %s\n" "$desc"
    ((pass++)) || true
  else
    check_finding "$desc" 1 "$note"
  fi
}

# Helper: assert a jq path is a number (0 is valid)
assert_number() {
  local path="$1"
  local desc="$2"
  local note="${3:-}"
  local val
  val=$(jq "$path" "$TMPDIR_LOCAL/out.json" 2>/dev/null)
  if [[ "$val" != "null" && "$val" != "" ]]; then
    printf "PASS: %s\n" "$desc"
    ((pass++)) || true
  else
    check_finding "$desc" 1 "$note"
  fi
}

echo ""
echo "--- tokens (all R for feature) ---"
assert_number ".tokens.input"          "tokens.input"
assert_number ".tokens.output"         "tokens.output"
assert_number ".tokens.cache_creation" "tokens.cache_creation"
assert_number ".tokens.cache_read"     "tokens.cache_read"
assert_number ".tokens.total"          "tokens.total"

echo ""
echo "--- cost (all R for feature) ---"
assert_number ".cost.net_usd"               "cost.net_usd"
assert_number ".cost.gross_usd"             "cost.gross_usd"
assert_field  ".cost.model"                 "cost.model"
assert_number ".cost.pricing.input"         "cost.pricing.input"
assert_number ".cost.pricing.output"        "cost.pricing.output"
assert_number ".cost.pricing.cache_read"    "cost.pricing.cache_read"
assert_number ".cost.pricing.cache_creation" "cost.pricing.cache_creation"

echo ""
echo "--- top-level scalars (all R for feature) ---"
assert_number ".turns"              "turns"
assert_number ".tool_calls"         "tool_calls"
assert_number ".wall_clock_minutes" "wall_clock_minutes" \
  "wall_clock_minutes comes from feature_metrics via ingest — check state.yaml timestamps"
assert_field  ".category"           "category"
assert_number ".human_interventions" "human_interventions"
assert_number ".rework_commits"     "rework_commits"
assert_number ".rework_rate"        "rework_rate"

echo ""
echo "--- resolution (R for feature) ---"
assert_number ".resolution.tasks_total"     "resolution.tasks_total"
assert_number ".resolution.tasks_planned"   "resolution.tasks_planned"
assert_number ".resolution.tasks_added"     "resolution.tasks_added"
assert_number ".resolution.tasks_completed" "resolution.tasks_completed"
assert_number ".resolution.tasks_failed"    "resolution.tasks_failed"
assert_number ".resolution.resolve_rate"    "resolution.resolve_rate"
# The following four fields are R for feature (metrics-schema.md) but
# ingest-feature-metrics.py::compute_retries() does NOT compute them.
# T-8's test masked this by seeding feature_metrics directly via upsert_feature_metrics().
# This integration test routes through the real ingest — these will be null if not computed.
assert_number ".resolution.pass_at_1" \
  "resolution.pass_at_1 [R for feature — scope-mismatch if null]" \
  "ingest does not compute pass_at_1 from state.yaml retries (T-10 gap)"
assert_number ".resolution.pass_at_2" \
  "resolution.pass_at_2 [R for feature — scope-mismatch if null]" \
  "ingest does not compute pass_at_2 from state.yaml retries (T-10 gap)"
assert_number ".resolution.regressions" \
  "resolution.regressions [R for feature — scope-mismatch if null]" \
  "ingest does not compute regressions from state.yaml (T-10 gap)"
assert_number ".resolution.regression_rate" \
  "resolution.regression_rate [R for feature — scope-mismatch if null]" \
  "ingest does not compute regression_rate from state.yaml (T-10 gap)"

echo ""
echo "--- retries (R for feature) ---"
assert_number ".retries.total" "retries.total"

echo ""
echo "--- churn (R for feature) ---"
assert_number ".churn.files_changed" "churn.files_changed"
assert_number ".churn.insertions"    "churn.insertions"
assert_number ".churn.deletions"     "churn.deletions"
assert_number ".churn.total_commits" "churn.total_commits"

echo ""
echo "--- reviews (R for feature) ---"
val=$(jq ".review_scores | type" "$TMPDIR_LOCAL/out.json" 2>/dev/null)
[[ "$val" == '"array"' ]]
check "review_scores is an array" $?

assert_number ".review_score_avg" "review_score_avg"

echo ""
echo "--- category value ---"
cat_val=$(jq -r ".category" "$TMPDIR_LOCAL/out.json" 2>/dev/null)
[[ "$cat_val" == "feature" ]]
check "category equals 'feature'" $?

echo ""
echo "--- benchmarks (R for feature) ---"
assert_number ".benchmarks.cost_per_task_usd"       "benchmarks.cost_per_task_usd"
assert_number ".benchmarks.cost_per_resolution_usd" "benchmarks.cost_per_resolution_usd"
assert_number ".benchmarks.tokens_per_task"         "benchmarks.tokens_per_task"
assert_number ".benchmarks.tokens_per_resolution"   "benchmarks.tokens_per_resolution"
assert_number ".benchmarks.input_output_ratio"      "benchmarks.input_output_ratio"
assert_number ".benchmarks.cache_hit_rate"          "benchmarks.cache_hit_rate"

echo ""
echo "--- per_agent_tokens / per_agent_tools (R for feature) ---"
val_pat=$(jq ".per_agent_tokens | type" "$TMPDIR_LOCAL/out.json" 2>/dev/null)
[[ "$val_pat" == '"string"' ]]
check "per_agent_tokens is a JSON string (not object)" $?

val_pats=$(jq ".per_agent_tools | type" "$TMPDIR_LOCAL/out.json" 2>/dev/null)
[[ "$val_pats" == '"string"' ]]
check "per_agent_tools is a JSON string (not object)" $?

echo ""
echo "--- per_step (R for feature) ---"
val_ps=$(jq ".per_step | type" "$TMPDIR_LOCAL/out.json" 2>/dev/null)
[[ "$val_ps" == '"object"' ]]
check "per_step is an object" $?

ps_keys=$(jq ".per_step | keys | length" "$TMPDIR_LOCAL/out.json" 2>/dev/null)
[[ "${ps_keys:-0}" -ge 1 ]]
check "per_step has at least one step entry" $?

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "Results: $pass passed, $fail failed"

if [[ ${#findings[@]} -gt 0 ]]; then
  echo ""
  echo "=== SCOPE-MISMATCH FINDINGS ==="
  echo "The following REQUIRED fields for schema=feature are NULL after routing"
  echo "through the real ingest pipeline (not bypassed via direct upsert)."
  echo ""
  for f in "${findings[@]}"; do
    echo "  FINDING: $f"
  done
  echo ""
  echo "Root cause: ingest-feature-metrics.py::compute_retries() (T-10) does not"
  echo "  compute pass_at_1, pass_at_2, regressions, regression_rate."
  echo "  T-8's test masked this because it seeds feature_metrics directly."
  echo "  Recommendation: expand compute_retries() to derive these from"
  echo "  state.yaml retries data (T-10 scope expansion, requires phase review)."
fi

[[ "$fail" -eq 0 ]]
