#!/usr/bin/env bash
# T-14 (RED) / T-15 (GREEN): narrow-contract test for read-sub-state-metrics.sh rewrite.
#
# Asserts output YAML has exactly the three top-level keys under `metrics:` that
# autopilot-session-rollup.sh reads:
#   metrics.tokens.total
#   metrics.duration_ms
#   metrics.churn.files_changed
#
# And that no extraneous keys are present (narrow contract preserved per OQ-5).
#
# RED: fails because the current read-sub-state-metrics.sh reads step_history
#      directly from state.yaml and does NOT call orchestrator metrics.
#      The rewritten script (T-15) projects from orchestrator metrics JSON,
#      making this test GREEN.
#
# Seeding: seeds DuckDB with known values, then invokes the script with a slug
# whose state.yaml is found via $HOME/.workflows/<slug>/state.yaml (active path).

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
SCRIPT="$REPO_ROOT/config/scripts/read-sub-state-metrics.sh"

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

# ── Setup temp working area ───────────────────────────────────────────────────
TMPDIR_LOCAL="$(mktemp -d "${TMPDIR:-/tmp}/read-sub-state-test-XXXXXX")"
trap 'rm -rf "$TMPDIR_LOCAL"' EXIT

SLUG="test-sub-state-aaaa"
FAKE_REPO="/test/repo"

export METRICS_DB="$TMPDIR_LOCAL/metrics.duckdb"
export PYTHONPATH="$REPO_ROOT/config/scripts"
export ORCHESTRATOR_HOME="$REPO_ROOT"

# Create a fake REPO_ROOT with archive path so the script can find state.yaml
# (We use REPO_ROOT env var to point the script to our temp tree)
FAKE_ROOT="$TMPDIR_LOCAL/fake-repo"
mkdir -p "$FAKE_ROOT/spec/changes/archive/$SLUG"
cat > "$FAKE_ROOT/spec/changes/archive/$SLUG/state.yaml" <<YAML
change_id: $SLUG
slug: $SLUG
schema: feature
status: completed
repo_root: $FAKE_REPO
started_at: "2026-04-20T10:00:00Z"
completed_at: "2026-04-20T11:00:00Z"
step_history: []
YAML
export REPO_ROOT="$FAKE_ROOT"

# Seed step_events + feature_metrics into the temp DB via Python
python3 - <<'PY'
import os, sys, json
import duckdb

sys.path.insert(0, os.environ["PYTHONPATH"])
from orchestrator_next.upsert import ensure_schema, upsert_synthetic_event, upsert_feature_metrics

METRICS_DB = os.environ["METRICS_DB"]
SLUG       = "test-sub-state-aaaa"
REPO_ROOT  = "/test/repo"

db = duckdb.connect(METRICS_DB)
ensure_schema(db)

# Two steps so duration_ms is a sum (not trivially equal to one step's value)
upsert_synthetic_event(
    db,
    {"repo_root": REPO_ROOT, "change_id": SLUG},
    agent_name="developer",
    step_id="explore",
    phase="specify",
    usage={
        "model": "claude-sonnet-4-6",
        "input_tokens": 10000,
        "output_tokens": 2000,
        "cache_read_input_tokens": 5000,
        "cache_creation_input_tokens": 2000,
        "cost_usd": 0.08,
        "duration_ms": 30000,
        "turns": 5,
        "tool_calls": {"Read": 10},
    },
    started_at="2026-04-20T10:00:00",
    ended_at="2026-04-20T10:00:30",
)
upsert_synthetic_event(
    db,
    {"repo_root": REPO_ROOT, "change_id": SLUG},
    agent_name="developer",
    step_id="implement",
    phase="implement",
    usage={
        "model": "claude-sonnet-4-6",
        "input_tokens": 8000,
        "output_tokens": 1500,
        "cache_read_input_tokens": 3000,
        "cache_creation_input_tokens": 1500,
        "cost_usd": 0.06,
        "duration_ms": 60000,
        "turns": 7,
        "tool_calls": {"Edit": 5, "Grep": 3},
    },
    started_at="2026-04-20T10:01:00",
    ended_at="2026-04-20T10:02:00",
)

# feature_metrics with known churn values
upsert_feature_metrics(
    db,
    REPO_ROOT,
    SLUG,
    schema_name="feature",
    tasks_total=5,
    tasks_planned=5,
    tasks_added=0,
    tasks_completed=5,
    tasks_failed=0,
    resolve_rate=1.0,
    pass_at_1=1.0,
    pass_at_2=1.0,
    regressions=0,
    regression_rate=0.0,
    retries_total=0,
    human_interventions=0,
    files_changed=7,
    insertions=200,
    deletions=50,
    total_commits=8,
    rework_commits=0,
    rework_rate=0.0,
    review_scores_json=json.dumps([9]),
    review_score_avg=9.0,
    wall_clock_minutes=90.0,
    source="test-narrow-fixture",
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

# ── Run read-sub-state-metrics.sh against the fixture ────────────────────────
set +e
OUTPUT=$(bash "$SCRIPT" "$SLUG" 2>"$TMPDIR_LOCAL/err.txt")
SCRIPT_EXIT=$?
set -e

check "script exits 0" $([[ "$SCRIPT_EXIT" -eq 0 ]] && echo 0 || echo 1)

if [[ "$SCRIPT_EXIT" -ne 0 ]]; then
  echo "stderr: $(cat "$TMPDIR_LOCAL/err.txt")"
  echo "stdout: $OUTPUT"
  echo "Results: $pass passed, $fail failed"
  exit 1
fi

# ── Assert: narrow contract — exactly the three required keys ─────────────────
echo ""
echo "--- required narrow keys ---"
check_contains "metrics: top-level key present"      "$OUTPUT" "^metrics:"
check_contains "tokens.total is present"             "$OUTPUT" "total:"
check_contains "duration_ms is present"              "$OUTPUT" "duration_ms:"
check_contains "churn.files_changed is present"      "$OUTPUT" "files_changed:"

# Assert tokens.total is a number > 0
TOK_VAL=$(echo "$OUTPUT" | grep "total:" | awk '{print $2}' | head -1)
if [[ -n "$TOK_VAL" && "$TOK_VAL" -gt 0 ]] 2>/dev/null; then
  check "tokens.total is > 0 (seeded data flows through)" 0
else
  check "tokens.total is > 0 (seeded data flows through)" 1
fi

# Assert files_changed matches seeded value (7)
CHURN_VAL=$(echo "$OUTPUT" | grep "files_changed:" | awk '{print $2}' | head -1)
if [[ "$CHURN_VAL" == "7" ]]; then
  check "churn.files_changed equals seeded value (7)" 0
else
  check "churn.files_changed equals seeded value (7) — got '$CHURN_VAL'" 1
fi

# Assert duration_ms is a number > 0 (sum of step durations: 30000 + 60000 = 90000)
DUR_VAL=$(echo "$OUTPUT" | grep "duration_ms:" | awk '{print $2}' | head -1)
if [[ -n "$DUR_VAL" && "$DUR_VAL" -gt 0 ]] 2>/dev/null; then
  check "duration_ms is > 0 (step durations flow through)" 0
else
  check "duration_ms is > 0 (step durations flow through)" 1
fi

echo ""
echo "--- narrow contract: no extra top-level keys under metrics: ---"
# The only sub-keys allowed under metrics: are: tokens (with total), duration_ms, churn (with files_changed)
# Verify gross keys that should NOT appear in narrow output
check_absent "cost: block is absent (narrow contract)" "$OUTPUT" "^  cost:"
check_absent "resolution: block is absent (narrow contract)" "$OUTPUT" "^  resolution:"
check_absent "benchmarks: block is absent (narrow contract)" "$OUTPUT" "^  benchmarks:"
check_absent "per_agent_tokens absent (narrow contract)" "$OUTPUT" "per_agent_tokens:"

echo ""
echo "Results: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
