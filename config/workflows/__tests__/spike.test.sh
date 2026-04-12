#!/usr/bin/env bash
# Test: spike.yaml complete-phase wiring
# Asserts spike.yaml has a complete phase with include: _complete-phase-spike
# and that the included file resolves to the two-step list.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
SPIKE_YAML="$REPO_ROOT/config/workflows/spike.yaml"
INCLUDE_YAML="$REPO_ROOT/config/workflows/_complete-phase-spike.yaml"

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

# spike.yaml must exist
if [[ ! -f "$SPIKE_YAML" ]]; then
  echo "FAIL: $SPIKE_YAML does not exist"
  exit 1
fi

# complete phase must exist under phases
HAS_COMPLETE=$(yq '.phases[] | select(.name == "complete") | .name' "$SPIKE_YAML" 2>/dev/null || true)
check "spike.yaml has a complete phase" "$HAS_COMPLETE" "complete"

# complete phase must have include: _complete-phase-spike
INCLUDE_VAL=$(yq '.phases[] | select(.name == "complete") | .include' "$SPIKE_YAML" 2>/dev/null || true)
check "complete phase include is _complete-phase-spike" "$INCLUDE_VAL" "_complete-phase-spike"

# The included file must resolve to exactly [compute-swe-metrics, archive-completed-change]
if [[ ! -f "$INCLUDE_YAML" ]]; then
  echo "FAIL: included file $INCLUDE_YAML does not exist"
  ((fail++))
else
  STEP_COUNT=$(yq '.steps | length' "$INCLUDE_YAML")
  check "included phase step count is 2" "$STEP_COUNT" "2"

  STEP0=$(yq '.steps[0]' "$INCLUDE_YAML")
  check "included phase step[0] is compute-swe-metrics" "$STEP0" "compute-swe-metrics"

  STEP1=$(yq '.steps[1]' "$INCLUDE_YAML")
  check "included phase step[1] is archive-completed-change" "$STEP1" "archive-completed-change"
fi

echo ""
echo "Results: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
