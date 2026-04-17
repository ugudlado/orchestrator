#!/usr/bin/env bash
# Test: _complete-phase.yaml step ordering
#
# FR-3, AC-1: The complete phase step order must be exactly:
#   compute-prediction-accuracy → run-learn-cycle → mark-change-completed
#   → compute-swe-metrics → archive-completed-change → remove-worktree
#
# mark-change-completed must appear BEFORE compute-swe-metrics so that
# completed_at is available when the metrics script runs.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PHASE_FILE="$REPO_ROOT/config/workflows/_complete-phase.yaml"

pass=0
fail=0

check() {
  local desc="$1"
  local result="$2"   # 0 = pass, 1 = fail
  if [[ "$result" -eq 0 ]]; then
    echo "PASS: $desc"
    ((pass++))
  else
    echo "FAIL: $desc"
    ((fail++))
  fi
}

echo "=== Test: _complete-phase.yaml step ordering ==="

[[ -f "$PHASE_FILE" ]]
check "phase file exists" $?

if [[ ! -f "$PHASE_FILE" ]]; then
  echo "Results: $pass passed, $fail failed"
  [[ "$fail" -eq 0 ]]; exit $?
fi

# Extract step list (lines under 'steps:' section)
# Steps are formatted as "  - stepname" (2-space indent + dash + space)
STEPS=$(awk '/^steps:/{in_steps=1; next}
  in_steps && /^  - /{line=$0; gsub(/^  - /, "", line); gsub(/ .*/, "", line); print line; next}
  in_steps && /^[a-z]/{in_steps=0}
' "$PHASE_FILE")

echo "Steps found:"
echo "$STEPS" | nl -ba

# Required steps in exact order
REQUIRED_ORDER=(
  "compute-prediction-accuracy"
  "run-learn-cycle"
  "mark-change-completed"
  "compute-swe-metrics"
  "archive-completed-change"
  "remove-worktree"
)

# Check each required step is present
for step in "${REQUIRED_ORDER[@]}"; do
  echo "$STEPS" | grep -q "$step"
  check "step '$step' is present" $?
done

# Check ordering: get line positions
get_pos() {
  echo "$STEPS" | grep -n "$1" | head -1 | cut -d: -f1
}

POS_PREDICT=$(get_pos "compute-prediction-accuracy")
POS_LEARN=$(get_pos "run-learn-cycle")
POS_MARK=$(get_pos "mark-change-completed")
POS_METRICS=$(get_pos "compute-swe-metrics")
POS_ARCHIVE=$(get_pos "archive-completed-change")
POS_REMOVE=$(get_pos "remove-worktree")

echo ""
echo "Step positions: predict=$POS_PREDICT learn=$POS_LEARN mark=$POS_MARK metrics=$POS_METRICS archive=$POS_ARCHIVE remove=$POS_REMOVE"

# All must be non-empty (present)
[[ -n "$POS_MARK" && -n "$POS_METRICS" ]]
check "both mark-change-completed and compute-swe-metrics have positions" $?

# Critical: mark-change-completed BEFORE compute-swe-metrics
if [[ -n "$POS_MARK" && -n "$POS_METRICS" ]]; then
  [[ "$POS_MARK" -lt "$POS_METRICS" ]]
  check "mark-change-completed (pos $POS_MARK) appears before compute-swe-metrics (pos $POS_METRICS)" $?
fi

# compute-swe-metrics BEFORE archive-completed-change
if [[ -n "$POS_METRICS" && -n "$POS_ARCHIVE" ]]; then
  [[ "$POS_METRICS" -lt "$POS_ARCHIVE" ]]
  check "compute-swe-metrics (pos $POS_METRICS) appears before archive-completed-change (pos $POS_ARCHIVE)" $?
fi

# run-learn-cycle BEFORE mark-change-completed
if [[ -n "$POS_LEARN" && -n "$POS_MARK" ]]; then
  [[ "$POS_LEARN" -lt "$POS_MARK" ]]
  check "run-learn-cycle (pos $POS_LEARN) appears before mark-change-completed (pos $POS_MARK)" $?
fi

echo ""
echo "Results: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
