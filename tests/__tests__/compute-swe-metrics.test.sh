#!/usr/bin/env bash
# Test: compute-swe-metrics.sh schema-dispatch
# Asserts:
#   - spike fixture → resolution fields null (~), no review_scores key, tokens/cost/churn present
#   - autopilot fixture → same reduced shape as spike
#   - feature fixture → full output (resolution with real values, review_scores key present)
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
SCRIPT="$REPO_ROOT/config/scripts/inline/compute-swe-metrics.sh"
FIXTURES_DIR="$(dirname "$0")/fixtures"

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

check_contains() {
  local desc="$1"
  local haystack="$2"
  local needle="$3"
  if echo "$haystack" | grep -q "$needle"; then
    echo "PASS: $desc"
    ((pass++))
  else
    echo "FAIL: $desc — output does not contain '$needle'"
    ((fail++))
  fi
}

check_absent() {
  local desc="$1"
  local haystack="$2"
  local needle="$3"
  if echo "$haystack" | grep -q "$needle"; then
    echo "FAIL: $desc — output should NOT contain '$needle' but does"
    ((fail++))
  else
    echo "PASS: $desc"
    ((pass++))
  fi
}

# Script must exist
if [[ ! -f "$SCRIPT" ]]; then
  echo "FAIL: $SCRIPT does not exist"
  exit 1
fi

# ── Setup: create per-schema temp dirs with state.yaml ───────────────────

TMPDIR_BASE="${TMPDIR:-/tmp}/hl278-metrics-test-$$"
mkdir -p "$TMPDIR_BASE"

# Spike fixture dir
SPIKE_DIR="$TMPDIR_BASE/spike"
mkdir -p "$SPIKE_DIR"
cp "$FIXTURES_DIR/state.spike.yaml" "$SPIKE_DIR/state.yaml"

# Feature fixture dir
FEATURE_DIR="$TMPDIR_BASE/feature"
mkdir -p "$FEATURE_DIR"
cp "$FIXTURES_DIR/state.feature.yaml" "$FEATURE_DIR/state.yaml"

# Autopilot fixture dir
AUTOPILOT_DIR="$TMPDIR_BASE/autopilot"
mkdir -p "$AUTOPILOT_DIR"
sed 's/^schema: spike$/schema: autopilot/' "$FIXTURES_DIR/state.spike.yaml" > "$AUTOPILOT_DIR/state.yaml"

cleanup() { rm -rf "$TMPDIR_BASE"; }
trap cleanup EXIT

# ── Test spike schema ─────────────────────────────────────────────────────
echo "--- spike schema ---"
SPIKE_OUT=$(bash "$SCRIPT" "$SPIKE_DIR" 2>/dev/null)

# Must contain resolution: key
check_contains "spike: output has resolution: key" "$SPIKE_OUT" "resolution:"

# resolution fields must be explicit null (~)
check_contains "spike: resolve_rate is ~" "$SPIKE_OUT" "resolve_rate: ~"
check_contains "spike: pass_at_1 is ~" "$SPIKE_OUT" "pass_at_1: ~"
check_contains "spike: pass_at_2 is ~" "$SPIKE_OUT" "pass_at_2: ~"
check_contains "spike: regression_rate is ~" "$SPIKE_OUT" "regression_rate: ~"
check_contains "spike: tasks_total is ~" "$SPIKE_OUT" "tasks_total: ~"

# review_scores must be absent
check_absent "spike: no review_scores key" "$SPIKE_OUT" "review_scores:"

# tokens, cost, churn must be present
check_contains "spike: tokens: present" "$SPIKE_OUT" "tokens:"
check_contains "spike: cost: present" "$SPIKE_OUT" "cost:"
check_contains "spike: churn: present" "$SPIKE_OUT" "churn:"

# ── Test autopilot schema ─────────────────────────────────────────────────
echo "--- autopilot schema ---"
AUTOPILOT_OUT=$(bash "$SCRIPT" "$AUTOPILOT_DIR" 2>/dev/null)

# resolution fields must be explicit null (~)
check_contains "autopilot: resolve_rate is ~" "$AUTOPILOT_OUT" "resolve_rate: ~"
check_contains "autopilot: pass_at_1 is ~" "$AUTOPILOT_OUT" "pass_at_1: ~"

# review_scores must be absent
check_absent "autopilot: no review_scores key" "$AUTOPILOT_OUT" "review_scores:"

# tokens, cost, churn must be present
check_contains "autopilot: tokens: present" "$AUTOPILOT_OUT" "tokens:"
check_contains "autopilot: cost: present" "$AUTOPILOT_OUT" "cost:"

# ── Test feature schema ───────────────────────────────────────────────────
echo "--- feature schema ---"
FEATURE_OUT=$(bash "$SCRIPT" "$FEATURE_DIR" 2>/dev/null)

# resolution fields must have real (non-null) values
check_contains "feature: resolve_rate is numeric" "$FEATURE_OUT" "resolve_rate:"
# review_scores must be present
check_contains "feature: review_scores present" "$FEATURE_OUT" "review_scores:"
# tokens must be present
check_contains "feature: tokens: present" "$FEATURE_OUT" "tokens:"

# resolve_rate must NOT be ~ for feature
if echo "$FEATURE_OUT" | grep -q "resolve_rate: ~"; then
  echo "FAIL: feature resolve_rate must not be null"
  ((fail++))
else
  echo "PASS: feature resolve_rate is not null"
  ((pass++))
fi

echo ""
echo "Results: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
