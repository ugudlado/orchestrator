#!/usr/bin/env bash
# Test: _complete-phase.yaml step ordering
#
# FR-7, AC-10: The complete phase step order after Stage C must be:
#   compute-prediction-accuracy → run-learn-cycle → mark-change-completed
#   → ingest-feature-metrics → compute-swe-metrics → archive-completed-change
#   → remove-worktree
#
# ingest-driver-auto and ingest-subagents-auto were removed in Stage C (T-26).
# mark-change-completed must appear BEFORE ingest-feature-metrics so that
# completed_at is available when the ingest step runs.
# ingest-feature-metrics must appear BEFORE compute-swe-metrics so that
# the feature_metrics row is populated before compute-swe-metrics queries it.
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

# Required steps in exact order (Stage C: no ingest-driver-auto or ingest-subagents-auto)
REQUIRED_ORDER=(
  "compute-prediction-accuracy"
  "run-learn-cycle"
  "mark-change-completed"
  "ingest-feature-metrics"
  "compute-swe-metrics"
  "archive-completed-change"
  "remove-worktree"
)

# Check each required step is present
for step in "${REQUIRED_ORDER[@]}"; do
  echo "$STEPS" | grep -q "$step"
  check "step '$step' is present" $?
done

# Stage C: ingest-driver-auto and ingest-subagents-auto must NOT be present
for removed_step in "ingest-driver-auto" "ingest-subagents-auto"; do
  echo "$STEPS" | grep -q "$removed_step"
  NOT_PRESENT=$?
  [[ "$NOT_PRESENT" -ne 0 ]]
  check "Stage C: step '$removed_step' is absent (deleted in T-26)" $?
done

# Check ordering: get line positions
get_pos() {
  echo "$STEPS" | grep -n "$1" | head -1 | cut -d: -f1
}

POS_PREDICT=$(get_pos "compute-prediction-accuracy")
POS_LEARN=$(get_pos "run-learn-cycle")
POS_MARK=$(get_pos "mark-change-completed")
POS_INGEST=$(get_pos "ingest-feature-metrics")
POS_METRICS=$(get_pos "compute-swe-metrics")
POS_ARCHIVE=$(get_pos "archive-completed-change")
POS_REMOVE=$(get_pos "remove-worktree")

echo ""
echo "Step positions: predict=$POS_PREDICT learn=$POS_LEARN mark=$POS_MARK ingest=$POS_INGEST metrics=$POS_METRICS archive=$POS_ARCHIVE remove=$POS_REMOVE"

# All must be non-empty (present) — FR-7, AC-10
[[ -n "$POS_MARK" && -n "$POS_INGEST" && -n "$POS_METRICS" ]]
check "mark-change-completed, ingest-feature-metrics, and compute-swe-metrics all have positions" $?

# Critical: mark-change-completed BEFORE ingest-feature-metrics
if [[ -n "$POS_MARK" && -n "$POS_INGEST" ]]; then
  [[ "$POS_MARK" -lt "$POS_INGEST" ]]
  check "mark-change-completed (pos $POS_MARK) appears before ingest-feature-metrics (pos $POS_INGEST)" $?
fi

# Critical: ingest-feature-metrics BEFORE compute-swe-metrics (AC-4, FR-7)
if [[ -n "$POS_INGEST" && -n "$POS_METRICS" ]]; then
  [[ "$POS_INGEST" -lt "$POS_METRICS" ]]
  check "ingest-feature-metrics (pos $POS_INGEST) appears before compute-swe-metrics (pos $POS_METRICS)" $?
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
