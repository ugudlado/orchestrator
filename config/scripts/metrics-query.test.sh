#!/usr/bin/env bash
# Test: metrics-query.sh named-query helper
# Asserts:
#   - each named query (cost-trend, retry-hotspots, cycle-count, quality-trend,
#     recent-features) returns rows on per-repo default
#   - --fleet aggregates across repos
#   - --repo <path> filters explicitly
#   - missing duckdb binary → exit non-zero + empty stdout
#   - missing DB file → exit non-zero + empty stdout
#   - zero-row query → exit non-zero + empty stdout
#   - no stderr on any failure path
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$REPO_ROOT/config/scripts/metrics-query.sh"

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
    echo "FAIL: $desc — expected empty output, got '$value'"
    ((fail++))
  fi
}

check_nonzero_exit() {
  local desc="$1"
  local exit_code="$2"
  if [[ "$exit_code" -ne 0 ]]; then
    echo "PASS: $desc"
    ((pass++))
  else
    echo "FAIL: $desc — expected non-zero exit, got 0"
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

# Script must exist before proceeding
if [[ ! -f "$SCRIPT" ]]; then
  echo "FAIL: $SCRIPT does not exist"
  exit 1
fi

# ── Preflight: duckdb must be available for fixture seeding ──────────────────
if ! command -v duckdb >/dev/null 2>&1; then
  echo "SKIP: duckdb not on PATH — cannot seed fixture DB"
  exit 0
fi

# ── Setup: fixture DB at $TMPDIR/test.duckdb ─────────────────────────────────
TEST_DB="${TMPDIR:-/tmp}/metrics-query-test-$$.duckdb"
REPO_A="/fake/repo-a"
REPO_B="/fake/repo-b"

# payload_json for repo-a: has step_history with retries and retry_reasons
PAYLOAD_A=$(cat <<'JSON'
{
  "metrics": {
    "cost_usd": 1.23,
    "quality_score": 8
  },
  "step_history": [
    {
      "step_id": "implement",
      "retries": 2,
      "retry_reasons": ["lint-failure", "type-error"]
    },
    {
      "step_id": "review",
      "retries": 1,
      "retry_reasons": ["missing-test"]
    }
  ]
}
JSON
)

# payload_json for repo-b: no step_history, minimal metrics
PAYLOAD_B=$(cat <<'JSON'
{
  "metrics": {
    "cost_usd": 0.45,
    "quality_score": 7
  }
}
JSON
)

# Escape single-quotes for SQL interpolation
sql_quote() { printf "%s" "${1//\'/\'\'}"; }

Q_PAYLOAD_A=$(sql_quote "$PAYLOAD_A")
Q_PAYLOAD_B=$(sql_quote "$PAYLOAD_B")

duckdb "$TEST_DB" <<SQL
CREATE TABLE IF NOT EXISTS features (
  repo_root      VARCHAR NOT NULL,
  change_id      VARCHAR NOT NULL,
  schema         VARCHAR,
  status         VARCHAR,
  started_at     VARCHAR,
  completed_at   VARCHAR,
  payload_json   VARCHAR,
  ingested_at    TIMESTAMP DEFAULT current_timestamp,
  PRIMARY KEY (repo_root, change_id)
);

INSERT INTO features (repo_root, change_id, schema, status, started_at, completed_at, payload_json)
VALUES
  ('$REPO_A', 'feature-alpha', 'feature', 'completed', '2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z', '$Q_PAYLOAD_A'),
  ('$REPO_A', 'feature-beta',  'feature', 'completed', '2026-01-03T00:00:00Z', '2026-01-04T00:00:00Z', '$Q_PAYLOAD_A'),
  ('$REPO_B', 'feature-gamma', 'feature', 'completed', '2026-01-05T00:00:00Z', '2026-01-06T00:00:00Z', '$Q_PAYLOAD_B');

CREATE TABLE IF NOT EXISTS step_history (
  repo_root     VARCHAR NOT NULL,
  change_id     VARCHAR NOT NULL,
  step_ord      INTEGER NOT NULL,
  step_id       VARCHAR,
  phase         VARCHAR,
  status        VARCHAR,
  agent         VARCHAR,
  started_at    VARCHAR,
  completed_at  VARCHAR,
  total_tokens  BIGINT,
  tool_uses     INTEGER,
  duration_ms   BIGINT,
  PRIMARY KEY (repo_root, change_id, step_ord)
);

CREATE TABLE IF NOT EXISTS per_agent_metrics (
  repo_root     VARCHAR NOT NULL,
  change_id     VARCHAR NOT NULL,
  agent         VARCHAR NOT NULL,
  total_tokens  BIGINT,
  cost_usd      DOUBLE,
  tool_uses     INTEGER,
  duration_ms   BIGINT,
  steps         INTEGER,
  PRIMARY KEY (repo_root, change_id, agent)
);

CREATE TABLE IF NOT EXISTS per_step_metrics (
  repo_root     VARCHAR NOT NULL,
  change_id     VARCHAR NOT NULL,
  step_id       VARCHAR NOT NULL,
  total_tokens  BIGINT,
  tool_uses     INTEGER,
  duration_ms   BIGINT,
  cost_usd      DOUBLE,
  PRIMARY KEY (repo_root, change_id, step_id)
);

-- Seed per_step_metrics for REPO_A/feature-alpha (2 rows with distinct costs)
INSERT INTO per_step_metrics (repo_root, change_id, step_id, total_tokens, tool_uses, duration_ms, cost_usd)
VALUES
  ('$REPO_A', 'feature-alpha', 'implement', 8000, 40, 2000000, 0.50),
  ('$REPO_A', 'feature-alpha', 'review',    2000, 10, 1000000, 0.20);

-- Seed per_agent_metrics for REPO_A/feature-alpha (3 agents: 2 normal, 1 outlier)
-- Mean duration: (1000 + 2000 + 8000) / 3 = 3666ms; outlier (agent-c at 8000) > 2x mean (7333)
INSERT INTO per_agent_metrics (repo_root, change_id, agent, total_tokens, cost_usd, tool_uses, duration_ms, steps)
VALUES
  ('$REPO_A', 'feature-alpha', 'agent-a', 3000, 0.20, 15, 1000, 2),
  ('$REPO_A', 'feature-alpha', 'agent-b', 4000, 0.30, 20, 2000, 3),
  ('$REPO_A', 'feature-alpha', 'agent-c', 1000, 0.20,  5, 8000, 1);
SQL

cleanup() { rm -f "$TEST_DB"; }
trap cleanup EXIT

# Export so metrics-query.sh resolves the fixture DB
export METRICS_DB="$TEST_DB"

# ── Named query: cost-trend (per-repo default via --repo) ────────────────────
echo "--- cost-trend ---"
OUT=$(bash "$SCRIPT" cost-trend --repo "$REPO_A" 2>/dev/null); EXIT=$?
check_zero_exit "cost-trend --repo repo-a: exits 0" "$EXIT"
check_nonempty  "cost-trend --repo repo-a: non-empty stdout" "$OUT"

# ── Named query: quality-trend (per-repo) ────────────────────────────────────
echo "--- quality-trend ---"
OUT=$(bash "$SCRIPT" quality-trend --repo "$REPO_A" 2>/dev/null); EXIT=$?
check_zero_exit "quality-trend --repo repo-a: exits 0" "$EXIT"
check_nonempty  "quality-trend --repo repo-a: non-empty stdout" "$OUT"

# ── Named query: cycle-count (per-repo) ──────────────────────────────────────
echo "--- cycle-count ---"
OUT=$(bash "$SCRIPT" cycle-count --repo "$REPO_A" 2>/dev/null); EXIT=$?
check_zero_exit "cycle-count --repo repo-a: exits 0" "$EXIT"
check_nonempty  "cycle-count --repo repo-a: non-empty stdout" "$OUT"

# ── Named query: recent-features (per-repo) ──────────────────────────────────
echo "--- recent-features ---"
OUT=$(bash "$SCRIPT" recent-features --repo "$REPO_A" 2>/dev/null); EXIT=$?
check_zero_exit "recent-features --repo repo-a: exits 0" "$EXIT"
check_nonempty  "recent-features --repo repo-a: non-empty stdout" "$OUT"

# ── Named query: retry-hotspots (per-repo, repo-a has step_history) ──────────
echo "--- retry-hotspots ---"
OUT=$(bash "$SCRIPT" retry-hotspots --repo "$REPO_A" 2>/dev/null); EXIT=$?
check_zero_exit "retry-hotspots --repo repo-a: exits 0" "$EXIT"
check_nonempty  "retry-hotspots --repo repo-a: non-empty stdout" "$OUT"

# ── --fleet aggregates across repos ──────────────────────────────────────────
echo "--- fleet aggregation ---"
FLEET_OUT=$(bash "$SCRIPT" recent-features --fleet 2>/dev/null); EXIT=$?
check_zero_exit "recent-features --fleet: exits 0" "$EXIT"
check_nonempty  "recent-features --fleet: non-empty stdout" "$FLEET_OUT"

# Fleet should contain rows from both repos
if echo "$FLEET_OUT" | grep -q "feature-alpha" && echo "$FLEET_OUT" | grep -q "feature-gamma"; then
  echo "PASS: --fleet output contains rows from both repos"
  ((pass++))
else
  echo "FAIL: --fleet output should contain rows from both repos"
  ((fail++))
fi

# ── --repo filters to only the requested repo ─────────────────────────────────
echo "--- --repo filter ---"
REPO_A_OUT=$(bash "$SCRIPT" recent-features --repo "$REPO_A" 2>/dev/null); EXIT=$?
check_zero_exit "--repo repo-a: exits 0" "$EXIT"

# repo-a output must NOT include repo-b's change-id
if echo "$REPO_A_OUT" | grep -q "feature-gamma"; then
  echo "FAIL: --repo repo-a output should not include repo-b rows (feature-gamma)"
  ((fail++))
else
  echo "PASS: --repo repo-a output excludes repo-b rows"
  ((pass++))
fi

# ── retry-hotspots returns zero rows for repo-b (no step_history) ────────────
echo "--- zero-row result ---"
ZERO_OUT=$(bash "$SCRIPT" retry-hotspots --repo "$REPO_B" 2>/dev/null); EXIT=$?
check_nonzero_exit "retry-hotspots --repo repo-b (no step_history): exits non-zero" "$EXIT"
check_empty        "retry-hotspots --repo repo-b (no step_history): empty stdout" "$ZERO_OUT"

# ── Missing DB file → exit non-zero + empty stdout ────────────────────────────
echo "--- missing DB ---"
MISSING_DB_STDERR=$( METRICS_DB="/nonexistent/path/test.duckdb" bash "$SCRIPT" cost-trend --fleet 2>&1 1>/dev/null )
MISSING_DB_OUT=$(    METRICS_DB="/nonexistent/path/test.duckdb" bash "$SCRIPT" cost-trend --fleet 2>/dev/null ); EXIT=$?
check_nonzero_exit "missing DB: exits non-zero" "$EXIT"
check_empty        "missing DB: empty stdout" "$MISSING_DB_OUT"
check_empty        "missing DB: no stderr" "$MISSING_DB_STDERR"

# ── Missing duckdb binary → exit non-zero + empty stdout ─────────────────────
echo "--- missing duckdb binary ---"
ORIG_PATH="$PATH"
NO_DUCK_STDERR=$( PATH="/usr/bin:/bin" bash "$SCRIPT" cost-trend --fleet 2>&1 1>/dev/null )
NO_DUCK_OUT=$(    PATH="/usr/bin:/bin" bash "$SCRIPT" cost-trend --fleet 2>/dev/null ); EXIT=$?
check_nonzero_exit "missing duckdb binary: exits non-zero" "$EXIT"
check_empty        "missing duckdb binary: empty stdout" "$NO_DUCK_OUT"
check_empty        "missing duckdb binary: no stderr" "$NO_DUCK_STDERR"

# ── Fresh-clone silent fallback: no DB → silent exit non-zero, no files created ─
echo "--- fresh-clone silent fallback ---"
FRESH_DB="${TMPDIR:-/tmp}/metrics-query-fresh-$$.duckdb"
# Ensure the file does not exist before the call
rm -f "$FRESH_DB"

FRESH_STDOUT=$( METRICS_DB="$FRESH_DB" bash "$SCRIPT" cost-trend --fleet 2>/dev/null )
FRESH_EXIT=$?
FRESH_STDERR=$(  METRICS_DB="$FRESH_DB" bash "$SCRIPT" cost-trend --fleet 2>&1 1>/dev/null )

check_nonzero_exit "fresh-clone fallback: exits non-zero when DB absent" "$FRESH_EXIT"
check_empty        "fresh-clone fallback: empty stdout when DB absent"   "$FRESH_STDOUT"
check_empty        "fresh-clone fallback: no stderr when DB absent"      "$FRESH_STDERR"

if [[ -e "$FRESH_DB" ]]; then
  echo "FAIL: fresh-clone fallback — DB file was created as side-effect ($FRESH_DB)"
  ((fail++))
  rm -f "$FRESH_DB"
else
  echo "PASS: fresh-clone fallback — no DB file created as side-effect"
  ((pass++))
fi

# ── Named query: step-cost-hotspots ─────────────────────────────────────────
echo "--- step-cost-hotspots ---"
OUT=$(bash "$SCRIPT" step-cost-hotspots --repo "$REPO_A" 2>/dev/null); EXIT=$?
check_zero_exit    "step-cost-hotspots --repo repo-a: exits 0" "$EXIT"
check_nonempty     "step-cost-hotspots --repo repo-a: non-empty stdout" "$OUT"

# Fleet aggregation
FLEET_STEP=$(bash "$SCRIPT" step-cost-hotspots --fleet 2>/dev/null); EXIT=$?
check_zero_exit    "step-cost-hotspots --fleet: exits 0" "$EXIT"
check_nonempty     "step-cost-hotspots --fleet: non-empty stdout" "$FLEET_STEP"

# Zero-row path: REPO_B has no per_step_metrics rows
ZERO_STEP=$(bash "$SCRIPT" step-cost-hotspots --repo "$REPO_B" 2>/dev/null); EXIT=$?
check_nonzero_exit "step-cost-hotspots --repo repo-b (no rows): exits non-zero" "$EXIT"
check_empty        "step-cost-hotspots --repo repo-b (no rows): empty stdout" "$ZERO_STEP"

# ── Named query: agent-cost-hotspots ─────────────────────────────────────────
echo "--- agent-cost-hotspots ---"
OUT=$(bash "$SCRIPT" agent-cost-hotspots --repo "$REPO_A" 2>/dev/null); EXIT=$?
check_zero_exit "agent-cost-hotspots --repo repo-a: exits 0" "$EXIT"
check_nonempty  "agent-cost-hotspots --repo repo-a: non-empty stdout" "$OUT"

# ── Named query: agent-duration-outliers ────────────────────────────────────
echo "--- agent-duration-outliers ---"
OUT=$(bash "$SCRIPT" agent-duration-outliers --repo "$REPO_A" 2>/dev/null); EXIT=$?
check_zero_exit "agent-duration-outliers --repo repo-a: exits 0" "$EXIT"
check_nonempty  "agent-duration-outliers --repo repo-a: non-empty stdout" "$OUT"
# agent-c has duration 8000ms; mean is (1000+2000+8000)/3=3666ms; 2x mean=7333ms; agent-c qualifies
if echo "$OUT" | grep -q "agent-c"; then
  echo "PASS: agent-duration-outliers returns the outlier agent (agent-c)"
  ((pass++))
else
  echo "FAIL: agent-duration-outliers — expected agent-c in output, got: $OUT"
  ((fail++))
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "Results: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
