#!/usr/bin/env bash
# Test: _complete-phase-spike.yaml structure
# Asserts steps list is exactly [compute-swe-metrics, archive-completed-change]
# and does NOT contain run-learn-cycle or compute-prediction-accuracy.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
YAML="$REPO_ROOT/config/workflows/_complete-phase-spike.yaml"

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

# File must exist
if [[ ! -f "$YAML" ]]; then
  echo "FAIL: $YAML does not exist"
  exit 1
fi

# Must have exactly 2 steps
STEP_COUNT=$(yq '.steps | length' "$YAML")
check "step count is 2" "$STEP_COUNT" "2"

# First step must be compute-swe-metrics
STEP0=$(yq '.steps[0]' "$YAML")
check "step[0] is compute-swe-metrics" "$STEP0" "compute-swe-metrics"

# Second step must be archive-completed-change
STEP1=$(yq '.steps[1]' "$YAML")
check "step[1] is archive-completed-change" "$STEP1" "archive-completed-change"

# Must NOT contain run-learn-cycle
HAS_LEARN=$(yq '.steps[] | select(. == "run-learn-cycle")' "$YAML" 2>/dev/null || true)
if [[ -z "$HAS_LEARN" ]]; then
  echo "PASS: run-learn-cycle absent"
  ((pass++))
else
  echo "FAIL: run-learn-cycle must not be in steps"
  ((fail++))
fi

# Must NOT contain compute-prediction-accuracy
HAS_PRED=$(yq '.steps[] | select(. == "compute-prediction-accuracy")' "$YAML" 2>/dev/null || true)
if [[ -z "$HAS_PRED" ]]; then
  echo "PASS: compute-prediction-accuracy absent"
  ((pass++))
else
  echo "FAIL: compute-prediction-accuracy must not be in steps"
  ((fail++))
fi

echo ""
echo "Results: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
