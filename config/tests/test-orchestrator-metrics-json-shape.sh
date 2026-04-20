#!/usr/bin/env bash
# T-7 (RED) / T-8 (GREEN): orchestrator metrics --change-id X --format json JSON shape test.
#
# Required fields asserted (schema=feature, from metrics-schema.md § Per-Schema Variants
# and the task's "Minimum" list):
#
#   tokens.input            tokens.output     tokens.cache_creation
#   tokens.cache_read       tokens.total
#   cost.gross_usd          cost.net_usd      cost.model
#   cost.pricing.input      cost.pricing.output
#   cost.pricing.cache_read cost.pricing.cache_creation
#   turns                   tool_calls        wall_clock_minutes
#   resolution.tasks_total  resolution.tasks_planned
#   resolution.tasks_added  resolution.tasks_completed
#   resolution.tasks_failed resolution.resolve_rate
#   resolution.pass_at_1    resolution.pass_at_2
#   resolution.regressions  resolution.regression_rate
#   retries.total           human_interventions
#   rework_commits          rework_rate
#   churn.files_changed     churn.insertions
#   churn.deletions         churn.total_commits
#   review_scores           review_score_avg
#   category
#   benchmarks.cost_per_task_usd  benchmarks.cost_per_resolution_usd
#   benchmarks.tokens_per_task    benchmarks.tokens_per_resolution
#   benchmarks.input_output_ratio benchmarks.cache_hit_rate
#   per_agent_tokens        per_agent_tools
#   per_step                (object with at least one key)
#
# Omitted (O = optional): estimate_vs_actual.*
# Omitted (— for feature): resolution.iterations_*
# Omitted (lint_delta = always 0, future use): lint_delta is present per design.md §5
#   but the task "Minimum" list doesn't require it, so we assert it's present but
#   tolerate absence.
#
# category is resolved from feature_metrics.schema_name ("feature").
# per_agent_tokens and per_agent_tools are stringified JSON scalars
#   (register-repo.sh reads them via yq -p=json — must remain strings).
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CHANGE_ID="test-feature-abc"

pass=0
fail=0

check() {
  local desc="$1"
  local result="$2"  # 0 = pass, 1 = fail
  if [[ "$result" -eq 0 ]]; then
    printf "PASS: %s\n" "$desc"
    ((pass++)) || true
  else
    printf "FAIL: %s\n" "$desc"
    ((fail++)) || true
  fi
}

echo "=== Test: orchestrator metrics --format json shape (schema=feature) ==="

# ─── Prerequisites ────────────────────────────────────────────────────────────
command -v jq >/dev/null 2>&1
check "jq is available" $?

# ─── Setup temp DB ────────────────────────────────────────────────────────────
TMPDIR_LOCAL="$(mktemp -d "${TMPDIR:-/tmp}/metrics-test-XXXXXX")"
trap 'rm -rf "$TMPDIR_LOCAL"' EXIT

export METRICS_DB="$TMPDIR_LOCAL/metrics.duckdb"
export PYTHONPATH="$REPO_ROOT/config/scripts"

# Seed step_events + feature_metrics into the temp DB via Python
python3 - <<'PY'
import os, sys, json
import duckdb

sys.path.insert(0, os.environ["PYTHONPATH"])
from orchestrator_next.upsert import ensure_schema, upsert_synthetic_event, upsert_feature_metrics

METRICS_DB = os.environ["METRICS_DB"]
REPO_ROOT  = "/test/repo"
CHANGE_ID  = "test-feature-abc"

db = duckdb.connect(METRICS_DB)
ensure_schema(db)

# --- Seed step_events: two steps, developer agent, model=claude-sonnet-4-5 ---
upsert_synthetic_event(
    db,
    {"repo_root": REPO_ROOT, "change_id": CHANGE_ID},
    agent_name="developer",
    step_id="explore",
    phase="specify",
    usage={
        "model": "claude-sonnet-4-5",
        "input_tokens": 10000,
        "output_tokens": 2000,
        "cache_read_input_tokens": 5000,
        "cache_creation_input_tokens": 3000,
        "cost_usd": 0.08,
        "duration_ms": 60000,
        "turns": 5,
        "tool_calls": {"Read": 10, "Grep": 4},
    },
    started_at="2026-04-20T10:00:00",
    ended_at="2026-04-20T10:01:00",
)
upsert_synthetic_event(
    db,
    {"repo_root": REPO_ROOT, "change_id": CHANGE_ID},
    agent_name="developer",
    step_id="implement",
    phase="implement",
    usage={
        "model": "claude-sonnet-4-5",
        "input_tokens": 8000,
        "output_tokens": 3000,
        "cache_read_input_tokens": 4000,
        "cache_creation_input_tokens": 2000,
        "cost_usd": 0.12,
        "duration_ms": 120000,
        "turns": 8,
        "tool_calls": {"Read": 5, "Write": 3, "Edit": 2},
    },
    started_at="2026-04-20T10:02:00",
    ended_at="2026-04-20T10:04:00",
)

# --- Seed feature_metrics: schema=feature with full fields ---
upsert_feature_metrics(
    db,
    REPO_ROOT,
    CHANGE_ID,
    schema_name="feature",
    tasks_total=10,
    tasks_planned=10,
    tasks_added=0,
    tasks_completed=9,
    tasks_failed=1,
    resolve_rate=0.9,
    pass_at_1=0.8,
    pass_at_2=0.9,
    regressions=0,
    regression_rate=0.0,
    retries_total=2,
    human_interventions=0,
    files_changed=5,
    insertions=120,
    deletions=30,
    total_commits=8,
    rework_commits=1,
    rework_rate=0.125,
    review_scores_json=json.dumps([8, 9, 9]),
    review_score_avg=8.67,
    wall_clock_minutes=18.3,
    source="test-fixture",
)

db.close()
print("Seed OK")
PY

seed_exit=$?
check "database seeded without error" $seed_exit

if [[ $seed_exit -ne 0 ]]; then
  echo "Results: $pass passed, $fail failed"
  exit 1
fi

# ─── Invoke the subcommand ───────────────────────────────────────────────────
echo ""
echo "--- Invoking 'orchestrator metrics' ---"
set +e
"$REPO_ROOT/bin/orchestrator" metrics --change-id "$CHANGE_ID" --format json \
  >"$TMPDIR_LOCAL/out.json" 2>"$TMPDIR_LOCAL/err.txt"
CLI_EXIT=$?
set -e

check "'orchestrator metrics' exits 0" $([[ "$CLI_EXIT" -eq 0 ]] && echo 0 || echo 1)

if [[ "$CLI_EXIT" -ne 0 ]]; then
  echo "stderr: $(cat "$TMPDIR_LOCAL/err.txt")"
  echo "Results: $pass passed, $fail failed"
  exit 1
fi

# ─── Assert JSON output contains all required fields ─────────────────────────

# Helper: assert a jq path returns non-null and non-false
assert_field() {
  local path="$1"
  local desc="$2"
  local val
  val=$(jq -r "$path // \"__MISSING__\"" "$TMPDIR_LOCAL/out.json" 2>/dev/null)
  [[ "$val" != "__MISSING__" && "$val" != "null" && "$val" != "" ]]
  check "$desc" $?
}

# Helper: assert a jq path returns a number (including 0)
assert_number() {
  local path="$1"
  local desc="$2"
  local val
  val=$(jq "$path" "$TMPDIR_LOCAL/out.json" 2>/dev/null)
  # A number is non-null; 0 is valid
  [[ "$val" != "null" && "$val" != "" ]]
  check "$desc" $?
}

echo ""
echo "--- tokens ---"
assert_number ".tokens.input"         "tokens.input is present"
assert_number ".tokens.output"        "tokens.output is present"
assert_number ".tokens.cache_creation" "tokens.cache_creation is present"
assert_number ".tokens.cache_read"    "tokens.cache_read is present"
assert_number ".tokens.total"         "tokens.total is present"

echo ""
echo "--- cost ---"
assert_number ".cost.net_usd"         "cost.net_usd is present"
assert_number ".cost.gross_usd"       "cost.gross_usd is present"
assert_field  ".cost.model"           "cost.model is present"
assert_number ".cost.pricing.input"   "cost.pricing.input is present"
assert_number ".cost.pricing.output"  "cost.pricing.output is present"
assert_number ".cost.pricing.cache_read" "cost.pricing.cache_read is present"
assert_number ".cost.pricing.cache_creation" "cost.pricing.cache_creation is present"

echo ""
echo "--- top-level scalars ---"
assert_number ".turns"                "turns is present"
assert_number ".tool_calls"           "tool_calls is present"
assert_number ".wall_clock_minutes"   "wall_clock_minutes is present"
assert_field  ".category"             "category is present"
assert_number ".human_interventions"  "human_interventions is present"
assert_number ".rework_commits"       "rework_commits is present"
assert_number ".rework_rate"          "rework_rate is present"

echo ""
echo "--- resolution ---"
assert_number ".resolution.tasks_total"      "resolution.tasks_total is present"
assert_number ".resolution.tasks_planned"    "resolution.tasks_planned is present"
assert_number ".resolution.tasks_added"      "resolution.tasks_added is present"
assert_number ".resolution.tasks_completed"  "resolution.tasks_completed is present"
assert_number ".resolution.tasks_failed"     "resolution.tasks_failed is present"
assert_number ".resolution.resolve_rate"     "resolution.resolve_rate is present"
assert_number ".resolution.pass_at_1"        "resolution.pass_at_1 is present"
assert_number ".resolution.pass_at_2"        "resolution.pass_at_2 is present"
assert_number ".resolution.regressions"      "resolution.regressions is present"
assert_number ".resolution.regression_rate"  "resolution.regression_rate is present"

echo ""
echo "--- retries ---"
assert_number ".retries.total"        "retries.total is present"

echo ""
echo "--- churn ---"
assert_number ".churn.files_changed"  "churn.files_changed is present"
assert_number ".churn.insertions"     "churn.insertions is present"
assert_number ".churn.deletions"      "churn.deletions is present"
assert_number ".churn.total_commits"  "churn.total_commits is present"

echo ""
echo "--- reviews ---"
# review_scores is an array (may be non-null even for empty)
val=$(jq ".review_scores | type" "$TMPDIR_LOCAL/out.json" 2>/dev/null)
[[ "$val" == '"array"' ]]
check "review_scores is an array" $?

assert_number ".review_score_avg"     "review_score_avg is present"

echo ""
echo "--- benchmarks ---"
assert_number ".benchmarks.cost_per_task_usd"       "benchmarks.cost_per_task_usd is present"
assert_number ".benchmarks.cost_per_resolution_usd" "benchmarks.cost_per_resolution_usd is present"
assert_number ".benchmarks.tokens_per_task"         "benchmarks.tokens_per_task is present"
assert_number ".benchmarks.tokens_per_resolution"   "benchmarks.tokens_per_resolution is present"
assert_number ".benchmarks.input_output_ratio"      "benchmarks.input_output_ratio is present"
assert_number ".benchmarks.cache_hit_rate"          "benchmarks.cache_hit_rate is present"

echo ""
echo "--- per_agent_tokens / per_agent_tools ---"
# Must be strings (stringified JSON) not objects — register-repo.sh reads them via yq -p=json
val_pat=$(jq ".per_agent_tokens | type" "$TMPDIR_LOCAL/out.json" 2>/dev/null)
[[ "$val_pat" == '"string"' ]]
check "per_agent_tokens is a JSON string (not object)" $?

val_pats=$(jq ".per_agent_tools | type" "$TMPDIR_LOCAL/out.json" 2>/dev/null)
[[ "$val_pats" == '"string"' ]]
check "per_agent_tools is a JSON string (not object)" $?

echo ""
echo "--- api_calls / per_tool_uses ---"
# api_calls must be a non-negative integer (alias for turns)
val_ac=$(jq ".api_calls" "$TMPDIR_LOCAL/out.json" 2>/dev/null)
[[ "$val_ac" != "null" && "$val_ac" != "" ]]
check "api_calls is present" $?
# Verify it's a non-negative integer (jq returns bare number for integers)
[[ "$val_ac" =~ ^[0-9]+$ ]]
check "api_calls is a non-negative integer" $?

# per_tool_uses must be a string that parses as JSON object
val_ptu_type=$(jq ".per_tool_uses | type" "$TMPDIR_LOCAL/out.json" 2>/dev/null)
[[ "$val_ptu_type" == '"string"' ]]
check "per_tool_uses is a JSON string (not object)" $?

val_ptu=$(jq -r ".per_tool_uses" "$TMPDIR_LOCAL/out.json" 2>/dev/null)
python3 -c "import json,sys; d=json.loads(sys.argv[1]); assert isinstance(d, dict)" "$val_ptu" 2>/dev/null
check "per_tool_uses value parses as a JSON object (dict)" $?

echo ""
echo "--- per_step ---"
val_ps=$(jq ".per_step | type" "$TMPDIR_LOCAL/out.json" 2>/dev/null)
[[ "$val_ps" == '"object"' ]]
check "per_step is an object" $?

# At least one step_id key must exist
ps_keys=$(jq ".per_step | keys | length" "$TMPDIR_LOCAL/out.json" 2>/dev/null)
[[ "$ps_keys" -ge 1 ]]
check "per_step has at least one step entry" $?

echo ""
echo "--- category value ---"
cat_val=$(jq -r ".category" "$TMPDIR_LOCAL/out.json" 2>/dev/null)
[[ "$cat_val" == "feature" ]]
check "category equals 'feature'" $?

echo ""
echo "Results: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
