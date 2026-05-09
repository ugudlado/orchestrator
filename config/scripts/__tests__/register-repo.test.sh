#!/usr/bin/env bash
# Test: register-repo.sh — DDL idempotency, child-row ingest, graceful-skip paths
# Asserts:
#   - schema contains all 4 tables after running register-repo.sh
#   - full-data fixture produces correct row counts in all 3 new tables
#   - partial fixture (no per_agent_tokens, no per_step): graceful skip, exit 0
#   - no-usage fixture: NULL numeric columns in step_history
#   - re-running ingest is idempotent (row counts unchanged)
#   - --rebuild deletes and re-ingests correctly
#   - per_agent_metrics rows accessible without json_extract
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
SCRIPT="$REPO_ROOT/config/scripts/register-repo.sh"

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

check_nonempty() {
  local desc="$1"
  local value="$2"
  if [[ -n "$value" ]]; then
    echo "PASS: $desc"
    ((pass++))
  else
    echo "FAIL: $desc — expected non-empty output, got empty"
    ((fail++))
  fi
}

check_empty() {
  local desc="$1"
  local value="$2"
  if [[ -z "$value" ]]; then
    echo "PASS: $desc"
    ((pass++))
  else
    echo "FAIL: $desc — expected empty, got '$value'"
    ((fail++))
  fi
}

check_zero_exit() {
  local desc="$1"
  local exit_code="$2"
  if [[ "$exit_code" -eq 0 ]]; then
    echo "PASS: $desc"
    ((pass++))
  else
    echo "FAIL: $desc — expected exit 0, got $exit_code"
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
    echo "FAIL: $desc — '$needle' not found in output"
    ((fail++))
  fi
}

# Script must exist before proceeding
if [[ ! -f "$SCRIPT" ]]; then
  echo "FAIL: $SCRIPT does not exist"
  exit 1
fi

# Tools must be available
if ! command -v duckdb >/dev/null 2>&1; then
  echo "SKIP: duckdb not on PATH"
  exit 0
fi

if ! command -v yq >/dev/null 2>&1; then
  echo "SKIP: yq not on PATH"
  exit 0
fi

# ── Setup: temp orchestrator home and fake repo ──────────────────────────────
TEST_DIR="${TMPDIR:-/tmp}/register-repo-test-$$"
FAKE_ORCHESTRATOR="$TEST_DIR/orchestrator"
FAKE_REPO="$TEST_DIR/fake-repo"
TEST_DB="$TEST_DIR/test.duckdb"
ARCHIVE_DIR="$FAKE_REPO/spec/changes/archive"

mkdir -p "$FAKE_ORCHESTRATOR"
mkdir -p "$FAKE_REPO/.git"  # make it look like a git repo
mkdir -p "$ARCHIVE_DIR"

export ORCHESTRATOR_HOME="$FAKE_ORCHESTRATOR"
export METRICS_DB="$TEST_DB"

cleanup() { rm -rf "$TEST_DIR"; }
trap cleanup EXIT

# ── T-1: Schema tables exist ──────────────────────────────────────────────────
echo ""
echo "=== T-1: Schema tables ==="

# Run register-repo.sh against a minimal fixture repo (no archives)
STDERR_T1=$(bash "$SCRIPT" "$FAKE_REPO" 2>&1 1>/dev/null)
EXIT_T1=$?
check_zero_exit "T-1: register-repo.sh exits 0" "$EXIT_T1"

# Assert all 4 tables exist
TABLES=$(duckdb -csv "$TEST_DB" "SELECT name FROM (SHOW TABLES) ORDER BY name" 2>/dev/null | tail -n +2 | tr '\n' ',')
check "T-1: features table exists"          "$(echo "$TABLES" | grep -c 'features')"          "1"
check "T-1: step_history table exists"      "$(echo "$TABLES" | grep -c 'step_history')"      "1"
check "T-1: per_agent_metrics table exists" "$(echo "$TABLES" | grep -c 'per_agent_metrics')" "1"
check "T-1: per_step_metrics table exists"  "$(echo "$TABLES" | grep -c 'per_step_metrics')"  "1"

# Assert idempotency: running twice does not error
STDERR_T1B=$(bash "$SCRIPT" "$FAKE_REPO" 2>&1 1>/dev/null)
EXIT_T1B=$?
check_zero_exit "T-1: second run exits 0 (idempotent schema)" "$EXIT_T1B"

# ── T-3: Full-data fixture ────────────────────────────────────────────────────
echo ""
echo "=== T-3: Full-data fixture (feature-full) ==="

FIXTURE_FULL="$ARCHIVE_DIR/feature-full"
mkdir -p "$FIXTURE_FULL"
cat > "$FIXTURE_FULL/state.yaml" <<'YAML'
schema: feature
change_id: feature-full
status: completed
started_at: "2026-01-01T00:00:00Z"
completed_at: "2026-01-02T00:00:00Z"
step_history:
  - step_id: implement
    phase: implementation
    status: completed
    agent: developer
    started_at: "2026-01-01T01:00:00Z"
    completed_at: "2026-01-01T02:00:00Z"
    usage:
      total_tokens: 1000
      tool_uses: 5
      duration_ms: 3600000
  - step_id: review
    phase: review
    status: completed
    started_at: "2026-01-01T03:00:00Z"
    completed_at: "2026-01-01T04:00:00Z"
    usage: {}
metrics:
  cost_usd: 0.05
  per_agent_tokens: '{"agent-a": {"total_tokens": 100, "cost_usd": 0.01, "tool_uses": 5, "duration_ms": 1000, "steps": 1}, "agent-b": {"total_tokens": 200, "cost_usd": 0.02, "tool_uses": 10, "duration_ms": 2000, "steps": 1}}'
  per_step:
    implement:
      total_tokens: 800
      tool_uses: 4
      duration_ms: 2000000
      cost_usd: 0.03
    review:
      total_tokens: 200
      tool_uses: 1
      duration_ms: 1600000
      cost_usd: 0.02
YAML

# Run ingest
bash "$SCRIPT" "$FAKE_REPO" 2>/dev/null
EXIT_FULL=$?
check_zero_exit "T-3: register-repo.sh exits 0 on full fixture" "$EXIT_FULL"

# Assert row counts
SH_COUNT=$(duckdb -csv "$TEST_DB" "SELECT COUNT(*) FROM step_history WHERE change_id='feature-full'" 2>/dev/null | tail -n +2)
PA_COUNT=$(duckdb -csv "$TEST_DB" "SELECT COUNT(*) FROM per_agent_metrics WHERE change_id='feature-full'" 2>/dev/null | tail -n +2)
PS_COUNT=$(duckdb -csv "$TEST_DB" "SELECT COUNT(*) FROM per_step_metrics WHERE change_id='feature-full'" 2>/dev/null | tail -n +2)

check "T-3: step_history has 2 rows for feature-full"      "$SH_COUNT" "2"
check "T-3: per_agent_metrics has 2 rows for feature-full" "$PA_COUNT" "2"
check "T-3: per_step_metrics has 2 rows for feature-full"  "$PS_COUNT" "2"

# Assert agent column on step with agent set
AGENT_VAL=$(duckdb -csv "$TEST_DB" "SELECT agent FROM step_history WHERE change_id='feature-full' AND step_id='implement'" 2>/dev/null | tail -n +2)
check "T-3: step_history.agent populated for implement step" "$AGENT_VAL" "developer"

# ── T-5a: Partial fixture (no per_agent_tokens, no per_step) ─────────────────
echo ""
echo "=== T-5a: Partial fixture (feature-partial) ==="

FIXTURE_PARTIAL="$ARCHIVE_DIR/feature-partial"
mkdir -p "$FIXTURE_PARTIAL"
cat > "$FIXTURE_PARTIAL/state.yaml" <<'YAML'
schema: feature
change_id: feature-partial
status: completed
started_at: "2026-01-03T00:00:00Z"
completed_at: "2026-01-04T00:00:00Z"
step_history:
  - step_id: implement
    phase: implementation
    status: completed
    agent: developer
    started_at: "2026-01-03T01:00:00Z"
    completed_at: "2026-01-03T02:00:00Z"
    usage:
      total_tokens: 500
      tool_uses: 3
      duration_ms: 1800000
metrics:
  cost_usd: 0.02
YAML

STDERR_PARTIAL=$(bash "$SCRIPT" "$FAKE_REPO" 2>&1 1>/dev/null)
EXIT_PARTIAL=$?
check_zero_exit "T-5a: register-repo.sh exits 0 on partial fixture" "$EXIT_PARTIAL"
check_empty     "T-5a: no stderr on partial fixture" "$STDERR_PARTIAL"

SH_P=$(duckdb -csv "$TEST_DB" "SELECT COUNT(*) FROM step_history WHERE change_id='feature-partial'" 2>/dev/null | tail -n +2)
PA_P=$(duckdb -csv "$TEST_DB" "SELECT COUNT(*) FROM per_agent_metrics WHERE change_id='feature-partial'" 2>/dev/null | tail -n +2)
PS_P=$(duckdb -csv "$TEST_DB" "SELECT COUNT(*) FROM per_step_metrics WHERE change_id='feature-partial'" 2>/dev/null | tail -n +2)

check "T-5a: step_history > 0 for feature-partial" "$SH_P" "1"
check "T-5a: per_agent_metrics = 0 for feature-partial" "$PA_P" "0"
check "T-5a: per_step_metrics = 0 for feature-partial" "$PS_P" "0"

# ── T-5b: No-usage fixture ────────────────────────────────────────────────────
echo ""
echo "=== T-5b: No-usage fixture (feature-no-usage) ==="

FIXTURE_NO_USAGE="$ARCHIVE_DIR/feature-no-usage"
mkdir -p "$FIXTURE_NO_USAGE"
cat > "$FIXTURE_NO_USAGE/state.yaml" <<'YAML'
schema: feature
change_id: feature-no-usage
status: completed
started_at: "2026-01-05T00:00:00Z"
completed_at: "2026-01-06T00:00:00Z"
step_history:
  - step_id: implement
    phase: implementation
    status: completed
    agent: developer
    started_at: "2026-01-05T01:00:00Z"
    completed_at: "2026-01-05T02:00:00Z"
metrics:
  cost_usd: 0.01
YAML

STDERR_NO_USAGE=$(bash "$SCRIPT" "$FAKE_REPO" 2>&1 1>/dev/null)
EXIT_NO_USAGE=$?
check_zero_exit "T-5b: register-repo.sh exits 0 on no-usage fixture" "$EXIT_NO_USAGE"
check_empty     "T-5b: no stderr on no-usage fixture" "$STDERR_NO_USAGE"

# The single step should have NULL numeric columns
# DuckDB CSV outputs literal 'NULL' for null values
NULL_CHECK=$(duckdb -csv "$TEST_DB" "SELECT total_tokens, tool_uses, duration_ms FROM step_history WHERE change_id='feature-no-usage'" 2>/dev/null | tail -n +2)
check "T-5b: step_history row has NULL numerics" "$NULL_CHECK" "NULL,NULL,NULL"

# ── T-7: Idempotency ──────────────────────────────────────────────────────────
echo ""
echo "=== T-7: Idempotency (feature-full, 2nd run) ==="

# Run a second time against the full fixture
bash "$SCRIPT" "$FAKE_REPO" 2>/dev/null
EXIT_IDEM=$?
check_zero_exit "T-7: second ingest exits 0" "$EXIT_IDEM"

SH_IDEM=$(duckdb -csv "$TEST_DB" "SELECT COUNT(*) FROM step_history WHERE change_id='feature-full'" 2>/dev/null | tail -n +2)
PA_IDEM=$(duckdb -csv "$TEST_DB" "SELECT COUNT(*) FROM per_agent_metrics WHERE change_id='feature-full'" 2>/dev/null | tail -n +2)
PS_IDEM=$(duckdb -csv "$TEST_DB" "SELECT COUNT(*) FROM per_step_metrics WHERE change_id='feature-full'" 2>/dev/null | tail -n +2)

check "T-7: step_history count unchanged after 2nd run" "$SH_IDEM" "2"
check "T-7: per_agent_metrics count unchanged after 2nd run" "$PA_IDEM" "2"
check "T-7: per_step_metrics count unchanged after 2nd run" "$PS_IDEM" "2"

# ── T-8: Rebuild ordering ──────────────────────────────────────────────────────
echo ""
echo "=== T-8: Rebuild ordering (--rebuild) ==="

STDERR_REBUILD=$(bash "$SCRIPT" --rebuild "$FAKE_REPO" 2>&1 1>/dev/null)
EXIT_REBUILD=$?
check_zero_exit "T-8: --rebuild exits 0" "$EXIT_REBUILD"
check_empty     "T-8: no stderr on rebuild" "$STDERR_REBUILD"

SH_REB=$(duckdb -csv "$TEST_DB" "SELECT COUNT(*) FROM step_history WHERE repo_root='$FAKE_REPO'" 2>/dev/null | tail -n +2)
PA_REB=$(duckdb -csv "$TEST_DB" "SELECT COUNT(*) FROM per_agent_metrics WHERE repo_root='$FAKE_REPO'" 2>/dev/null | tail -n +2)
PS_REB=$(duckdb -csv "$TEST_DB" "SELECT COUNT(*) FROM per_step_metrics WHERE repo_root='$FAKE_REPO'" 2>/dev/null | tail -n +2)
FEAT_REB=$(duckdb -csv "$TEST_DB" "SELECT COUNT(*) FROM features WHERE repo_root='$FAKE_REPO'" 2>/dev/null | tail -n +2)

# After rebuild, counts must match original ingest (3 features: full + partial + no-usage)
check "T-8: features row count correct after rebuild" "$FEAT_REB" "3"
# step_history: 2 (feature-full) + 1 (feature-partial) + 1 (feature-no-usage) = 4
check "T-8: step_history count correct after rebuild" "$SH_REB" "4"
# per_agent_metrics: 2 (feature-full only)
check "T-8: per_agent_metrics count correct after rebuild" "$PA_REB" "2"
# per_step_metrics: 2 (feature-full only)
check "T-8: per_step_metrics count correct after rebuild" "$PS_REB" "2"

# ── T-DN1: Dirname fallback positive (legacy-feature) ────────────────────────
echo ""
echo "=== T-DN1: Dirname fallback (legacy-feature) ==="

FIXTURE_LEGACY="$ARCHIVE_DIR/legacy-feature"
mkdir -p "$FIXTURE_LEGACY"
cat > "$FIXTURE_LEGACY/state.yaml" <<'YAML'
schema: feature
status: completed
started_at: "2026-02-01T00:00:00Z"
completed_at: "2026-02-02T00:00:00Z"
step_history:
  - step_id: implement
    phase: implementation
    status: completed
    agent: developer
    started_at: "2026-02-01T01:00:00Z"
    completed_at: "2026-02-01T02:00:00Z"
    usage:
      total_tokens: 100
      tool_uses: 2
      duration_ms: 60000
YAML

STDERR_DN1=$(bash "$SCRIPT" "$FAKE_REPO" 2>&1 1>/dev/null)
EXIT_DN1=$?
check_zero_exit "T-DN1: exits 0 with missing change_id" "$EXIT_DN1"
check_contains  "T-DN1: warn emitted for dirname fallback" "$STDERR_DN1" "warn: change_id absent, using dirname fallback: legacy-feature"

SH_DN1=$(duckdb -csv "$TEST_DB" "SELECT COUNT(*) FROM step_history WHERE change_id='legacy-feature'" 2>/dev/null | tail -n +2)
check "T-DN1: step_history has 1 row for legacy-feature" "$SH_DN1" "1"

# ── T-DN2: Dirname fallback hits slug guard (Bad_Slug) ───────────────────────
echo ""
echo "=== T-DN2: Dirname fallback hits slug guard (Bad_Slug) ==="

FIXTURE_BAD="$ARCHIVE_DIR/Bad_Slug"
mkdir -p "$FIXTURE_BAD"
cat > "$FIXTURE_BAD/state.yaml" <<'YAML'
schema: feature
status: completed
started_at: "2026-02-01T00:00:00Z"
completed_at: "2026-02-02T00:00:00Z"
step_history:
  - step_id: implement
    phase: implementation
    status: completed
    agent: developer
    started_at: "2026-02-01T01:00:00Z"
    completed_at: "2026-02-01T02:00:00Z"
YAML

STDERR_DN2=$(bash "$SCRIPT" "$FAKE_REPO" 2>&1 1>/dev/null)
EXIT_DN2=$?
check_zero_exit "T-DN2: exits 0 when slug guard rejects dirname" "$EXIT_DN2"
check_contains  "T-DN2: dirname fallback warn emitted" "$STDERR_DN2" "warn: change_id absent, using dirname fallback: Bad_Slug"
check_contains  "T-DN2: slug guard skip warn emitted" "$STDERR_DN2" "skip: change_id has unsafe chars: Bad_Slug"

SH_DN2=$(duckdb -csv "$TEST_DB" "SELECT COUNT(*) FROM step_history WHERE change_id='Bad_Slug'" 2>/dev/null | tail -n +2)
check "T-DN2: no rows ingested for Bad_Slug" "$SH_DN2" "0"

# ── T-13: json_extract-free consumer check (AC-3) ──────────────────────────────
echo ""
echo "=== T-13: json_extract-free access to per_agent_metrics ==="

PA_QUERY="SELECT agent, total_tokens FROM per_agent_metrics WHERE change_id='feature-full' ORDER BY agent"
PA_OUT=$(duckdb -csv "$TEST_DB" "$PA_QUERY" 2>/dev/null | tail -n +2)
check_nonempty "T-13: per_agent_metrics query returns rows" "$PA_OUT"
check_contains "T-13: agent-a row present" "$PA_OUT" "agent-a"
check_contains "T-13: agent-b row present" "$PA_OUT" "agent-b"

# Assert SQL itself contains no json_extract
if echo "$PA_QUERY" | grep -q "json_extract"; then
  echo "FAIL: T-13: query uses json_extract"
  ((fail++))
else
  echo "PASS: T-13: query contains no json_extract"
  ((pass++))
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "Results: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
