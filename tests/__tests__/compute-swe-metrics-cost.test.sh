#!/usr/bin/env bash
# Test: compute-swe-metrics.sh cost_usd inference from agent_pricing
# Asserts:
#   cost_usd_inferred_from_agent_pricing_when_step_cost_absent:
#     When a step_history entry has total_tokens=50000 but no cost_usd field,
#     compute-swe-metrics.sh must infer cost_usd = 50000 * 3.0 / 1000000 = 0.15
#     for agent "developer" (mapped to claude-sonnet-4-6, input_per_1m=3.0).
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
SCRIPT="$REPO_ROOT/config/scripts/inline/compute-swe-metrics.sh"
FIXTURES_DIR="$(dirname "$0")/fixtures"
FIXTURE="$FIXTURES_DIR/state.native-agent.yaml"

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

# ── Prerequisites ─────────────────────────────────────────────────────────
if [[ ! -f "$SCRIPT" ]]; then
  echo "SKIP: compute-swe-metrics.sh not found at $SCRIPT"
  exit 0
fi
if ! command -v duckdb >/dev/null 2>&1; then
  echo "SKIP: duckdb not installed"
  exit 0
fi

# ── Temp dir setup ────────────────────────────────────────────────────────
# Use a temp dir as STATE_DIR so the script doesn't touch the real metrics DB.
TEST_DIR=$(mktemp -d "${TMPDIR:-/tmp}/compute-swe-cost-test-XXXXXX")
TEST_DB="$TEST_DIR/test-metrics.duckdb"
trap 'rm -rf "$TEST_DIR"' EXIT

# Copy fixture state.yaml into the temp STATE_DIR.
cp "$FIXTURE" "$TEST_DIR/state.yaml"

# ── Seed agent_pricing into the temp DB ──────────────────────────────────
# Self-contained: does NOT depend on T-2 (register-repo.sh) being implemented.
# developer maps to native_sonnet -> claude-sonnet-4-6 -> input_per_1m=3.0
duckdb "$TEST_DB" <<'SQL'
CREATE TABLE IF NOT EXISTS agent_pricing (
  agent           VARCHAR PRIMARY KEY,
  model           VARCHAR,
  backend         VARCHAR,
  input_per_1m    DOUBLE,
  output_per_1m   DOUBLE,
  cache_read_per_1m DOUBLE
);
INSERT OR REPLACE INTO agent_pricing VALUES
  ('architect',         'claude-opus-4-7',    'native_opus',   15.00, 75.00, 1.50),
  ('ideator',           'claude-opus-4-7',    'native_opus',   15.00, 75.00, 1.50),
  ('reviewer',          'claude-sonnet-4-6',  'native_sonnet',  3.00, 15.00, 0.30),
  ('developer',         'claude-sonnet-4-6',  'native_sonnet',  3.00, 15.00, 0.30),
  ('discoverer',        'claude-sonnet-4-6',  'native_sonnet',  3.00, 15.00, 0.30),
  ('workflow-improver', 'claude-sonnet-4-6',  'native_sonnet',  3.00, 15.00, 0.30),
  ('sonnet-agent',      'claude-sonnet-4-6',  'native_sonnet',  3.00, 15.00, 0.30),
  ('haiku-agent',       'claude-sonnet-4-6',  'native_sonnet',  3.00, 15.00, 0.30);
SQL

# ── Run compute-swe-metrics.sh against the fixture ───────────────────────
# Pass METRICS_DB so the script can find agent_pricing in our temp DB.
METRICS_OUTPUT=$(METRICS_DB="$TEST_DB" ORCHESTRATOR_HOME="$REPO_ROOT" bash "$SCRIPT" "$TEST_DIR" 2>/dev/null)

# ── Parse per_agent_tokens from output ───────────────────────────────────
# Output line format: "  per_agent_tokens: '{"developer":{...}}'"
PER_AGENT_JSON=$(echo "$METRICS_OUTPUT" | grep 'per_agent_tokens:' | sed "s/.*per_agent_tokens: '//; s/'$//")

# Extract developer cost_usd from JSON (no jq dependency — use grep+awk)
# Format: "developer":{"total_tokens":50000,"cost_usd":0.150000,...}
DEVELOPER_COST=$(echo "$PER_AGENT_JSON" | grep -o '"developer":{[^}]*}' | grep -o '"cost_usd":[0-9.]*' | cut -d: -f2)

# Extract unknown-agent cost_usd — agent not in agent_pricing, cost must stay 0
UNKNOWN_COST=$(echo "$PER_AGENT_JSON" | grep -o '"unknown-agent":{[^}]*}' | grep -o '"cost_usd":[0-9.]*' | cut -d: -f2)

# ── Test: cost_usd_inferred_from_agent_pricing_when_step_cost_absent ─────
# Expected: 50000 * 3.0 / 1000000 = 0.15 (formatted as %.6f = 0.150000)
check "cost_usd_inferred_from_agent_pricing_when_step_cost_absent" \
  "$DEVELOPER_COST" \
  "0.150000"

# ── Test: unknown_agent_cost_stays_zero_when_not_in_agent_pricing ─────────
# unknown-agent has total_tokens=30000 but no agent_pricing row.
# The awk loop must advance past it without short-circuiting the known agents.
# Expected: cost_usd = 0.000000 (no pricing row => no inference)
check "unknown_agent_cost_stays_zero_when_not_in_agent_pricing" \
  "$UNKNOWN_COST" \
  "0.000000"

# ── Summary ──────────────────────────────────────────────────────────────
echo ""
echo "Results: $pass passed, $fail failed"
if [[ "$fail" -gt 0 ]]; then
  exit 1
fi
exit 0
