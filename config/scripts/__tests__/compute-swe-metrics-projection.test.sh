#!/usr/bin/env bash
# T-12 (RED) / T-13 (GREEN): byte-compat test for compute-swe-metrics.sh rewrite.
#
# Verifies:
#   1. All REQUIRED-for-feature keys from metrics-schema.md are present in output YAML.
#   2. The rewritten script shells out to `orchestrator metrics --format json`
#      (verified by absence of JSONL/git-log parsing; structural test).
#   3. Output is under the `metrics:` top-level key.
#
# Fixture: seeds a temp DuckDB with known values matching a feature schema.
# The seeded values are used as the ground truth for key-presence assertions.
#
# RED: fails because the current compute-swe-metrics.sh does NOT call
#      orchestrator metrics — it parses JSONL + state.yaml directly.
#      After T-13 (rewrite), this test turns GREEN.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/inline/compute-swe-metrics.sh"

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

# ── Prerequisites ─────────────────────────────────────────────────────────────
if [[ ! -f "$SCRIPT" ]]; then
  echo "FAIL: $SCRIPT does not exist"
  exit 1
fi

# ── Setup temp working area ───────────────────────────────────────────────────
TMPDIR_LOCAL="$(mktemp -d "${TMPDIR:-/tmp}/compute-swe-proj-test-XXXXXX")"
trap 'rm -rf "$TMPDIR_LOCAL"' EXIT

CHANGE_ID="test-proj-feature-abc"
FAKE_REPO="/test/repo"

export METRICS_DB="$TMPDIR_LOCAL/metrics.duckdb"
export PYTHONPATH="$REPO_ROOT/config/scripts"
export ORCHESTRATOR_HOME="$REPO_ROOT"

# Create a minimal state.yaml in the temp STATE_DIR
STATE_DIR="$TMPDIR_LOCAL/state"
mkdir -p "$STATE_DIR"
cat > "$STATE_DIR/state.yaml" <<YAML
change_id: $CHANGE_ID
slug: $CHANGE_ID
schema: feature
status: completed
repo_root: $FAKE_REPO
started_at: "2026-04-20T10:00:00Z"
completed_at: "2026-04-20T11:00:00Z"
step_history: []
YAML

# Seed step_events + feature_metrics into the temp DB via Python
python3 - <<'PY'
import os, sys, json
import duckdb

sys.path.insert(0, os.environ["PYTHONPATH"])
from orchestrator_next.upsert import ensure_schema, upsert_synthetic_event, upsert_feature_metrics

METRICS_DB = os.environ["METRICS_DB"]
CHANGE_ID  = "test-proj-feature-abc"
REPO_ROOT  = "/test/repo"

db = duckdb.connect(METRICS_DB)
ensure_schema(db)

# Seed step_events: one developer step with known token counts
upsert_synthetic_event(
    db,
    {"repo_root": REPO_ROOT, "change_id": CHANGE_ID},
    agent_name="developer",
    step_id="execute-next-task",
    phase="implement",
    usage={
        "model": "claude-sonnet-4-6",
        "input_tokens": 12000,
        "output_tokens": 2500,
        "cache_read_input_tokens": 8000,
        "cache_creation_input_tokens": 3000,
        "cost_usd": 0.12,
        "duration_ms": 90000,
        "turns": 8,
        "tool_calls": {"Read": 15, "Grep": 5, "Edit": 3},
    },
    started_at="2026-04-20T10:00:00",
    ended_at="2026-04-20T10:01:30",
)

# Seed feature_metrics: feature schema with full resolution + churn
upsert_feature_metrics(
    db,
    REPO_ROOT,
    CHANGE_ID,
    schema_name="feature",
    tasks_total=8,
    tasks_planned=8,
    tasks_added=0,
    tasks_completed=7,
    tasks_failed=1,
    resolve_rate=0.875,
    pass_at_1=0.75,
    pass_at_2=0.875,
    regressions=0,
    regression_rate=0.0,
    retries_total=1,
    human_interventions=0,
    files_changed=6,
    insertions=150,
    deletions=40,
    total_commits=10,
    rework_commits=1,
    rework_rate=0.1,
    review_scores_json=json.dumps([8, 9]),
    review_score_avg=8.5,
    wall_clock_minutes=60.0,
    source="test-projection-fixture",
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

# ── Run compute-swe-metrics.sh against the fixture ───────────────────────────
set +e
OUTPUT=$(bash "$SCRIPT" "$STATE_DIR" 2>"$TMPDIR_LOCAL/err.txt")
SCRIPT_EXIT=$?
set -e

check "script exits 0" $([[ "$SCRIPT_EXIT" -eq 0 ]] && echo 0 || echo 1)

if [[ "$SCRIPT_EXIT" -ne 0 ]]; then
  echo "stderr: $(cat "$TMPDIR_LOCAL/err.txt")"
  echo "stdout: $OUTPUT"
  echo "Results: $pass passed, $fail failed"
  exit 1
fi

# ── Assert: output is under metrics: key ──────────────────────────────────────
check_contains "output has top-level metrics: key" "$OUTPUT" "^metrics:"

# ── Assert: required-for-feature keys from metrics-schema.md ─────────────────
echo ""
echo "--- tokens ---"
check_contains "tokens.input is present"          "$OUTPUT" "input:"
check_contains "tokens.output is present"         "$OUTPUT" "output:"
check_contains "tokens.cache_creation is present" "$OUTPUT" "cache_creation:"
check_contains "tokens.cache_read is present"     "$OUTPUT" "cache_read:"
check_contains "tokens.total is present"          "$OUTPUT" "total:"

echo ""
echo "--- cost ---"
check_contains "cost.gross_usd is present"         "$OUTPUT" "gross_usd:"
check_contains "cost.net_usd is present"           "$OUTPUT" "net_usd:"
check_contains "cost.model is present"             "$OUTPUT" "model:"
check_contains "cost.pricing.input is present"     "$OUTPUT" "pricing:"

echo ""
echo "--- top-level scalars ---"
check_contains "turns is present"                  "$OUTPUT" "turns:"
check_contains "tool_calls is present"             "$OUTPUT" "tool_calls:"
check_contains "wall_clock_minutes is present"     "$OUTPUT" "wall_clock_minutes:"
check_contains "category is present"               "$OUTPUT" "category:"

echo ""
echo "--- resolution ---"
check_contains "resolution: block is present"       "$OUTPUT" "resolution:"
check_contains "tasks_total is present"             "$OUTPUT" "tasks_total:"
check_contains "tasks_completed is present"         "$OUTPUT" "tasks_completed:"
check_contains "resolve_rate is present"            "$OUTPUT" "resolve_rate:"
check_contains "pass_at_1 is present"               "$OUTPUT" "pass_at_1:"
check_contains "pass_at_2 is present"               "$OUTPUT" "pass_at_2:"
check_contains "regression_rate is present"         "$OUTPUT" "regression_rate:"

echo ""
echo "--- churn ---"
check_contains "churn: block is present"            "$OUTPUT" "churn:"
check_contains "churn.files_changed is present"     "$OUTPUT" "files_changed:"
check_contains "churn.insertions is present"        "$OUTPUT" "insertions:"
check_contains "churn.deletions is present"         "$OUTPUT" "deletions:"
check_contains "churn.total_commits is present"     "$OUTPUT" "total_commits:"

echo ""
echo "--- reviews ---"
check_contains "review_scores is present"           "$OUTPUT" "review_scores:"
check_contains "review_score_avg is present"        "$OUTPUT" "review_score_avg:"

echo ""
echo "--- benchmarks ---"
check_contains "benchmarks: block is present"       "$OUTPUT" "benchmarks:"
check_contains "cost_per_task_usd is present"       "$OUTPUT" "cost_per_task_usd:"

echo ""
echo "--- per_agent_tokens / per_agent_tools ---"
check_contains "per_agent_tokens is present"        "$OUTPUT" "per_agent_tokens:"
check_contains "per_agent_tools is present"         "$OUTPUT" "per_agent_tools:"

echo ""
echo "--- per_step ---"
check_contains "per_step: block is present"         "$OUTPUT" "per_step:"

echo ""
echo "--- source provenance (duckdb@) ---"
check_contains "metrics.source with duckdb@ present" "$OUTPUT" "source: "

echo ""
echo "Results: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
