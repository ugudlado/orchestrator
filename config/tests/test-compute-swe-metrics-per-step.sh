#!/usr/bin/env bash
# Test: compute-swe-metrics.sh per-step aggregation (metrics.per_step block)
#
# FR-7, AC-7: Given a state.yaml with mixed agent + inline step_history entries
# across distinct step_ids (including retries), compute-swe-metrics.sh must emit
# a metrics.per_step: block with one entry per distinct step_id, each containing:
#   total_tokens, tool_uses, duration_ms, executions (retry-inclusive)
#
# T-11: RED test — per_step block not yet emitted by the script.
# T-12: GREEN — after adding the awk pass.
# T-13: Token sum assertion (per_step totals == metrics.tokens.total ± 1%).
#
# NOTE on AC-7 scope: per_step aggregates step_history.usage data. The
# sum-equals-total assertion (T-13) holds when metrics.tokens.total also comes
# from step_history (i.e., when JSONL is absent). In production when JSONL is
# present, JSONL-derived totals may differ from step_history sums because JSONL
# includes orchestrator tokens not captured in step_history. The test fixture
# deliberately omits JSONL to ensure the assertion is well-defined.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$REPO_ROOT/config/scripts/inline/compute-swe-metrics.sh"

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

echo "=== Test: compute-swe-metrics per-step block ==="

[[ -f "$SCRIPT" ]]
check "compute-swe-metrics.sh exists" $?
if [[ ! -f "$SCRIPT" ]]; then
  echo "Results: $pass passed, $fail failed"
  [[ "$fail" -eq 0 ]]; exit $?
fi

# ── Fixture ───────────────────────────────────────────────────────────────
TMPDIR_BASE="${TMPDIR:-/tmp}/test-per-step-$$"
mkdir -p "$TMPDIR_BASE"
cleanup() { rm -rf "$TMPDIR_BASE"; }
trap cleanup EXIT

STATE_DIR="$TMPDIR_BASE/state"
mkdir -p "$STATE_DIR"

# Fixture: 3 distinct step_ids, one repeated (simulate retry)
# step_id A: 1 entry, 2000 tokens, 5 tool_uses, 10000 ms
# step_id B: 2 entries (retry), 1500 + 1800 tokens, 3+4 tool_uses, 8000+9000 ms
# step_id C (inline): 1 entry, 0 tokens (inline), 2 tool_uses, 5000 ms
#
# Expected per_step:
#   execute-next-task: total_tokens=2000, tool_uses=5, duration_ms=10000, executions=1
#   run-phase-review:  total_tokens=3300, tool_uses=7, duration_ms=17000, executions=2
#   mark-change-completed: total_tokens=0, tool_uses=2, duration_ms=5000, executions=1

cat > "$STATE_DIR/state.yaml" <<'STATEYAML'
change_id: test-per-step-001
slug: test-per-step-001
schema: feature
status: completed
started_at: "2026-01-15T10:00:00Z"
completed_at: "2026-01-15T11:00:00Z"
step_history:
  - step_id: execute-next-task
    phase: implement
    status: completed
    agent: developer
    started_at: "2026-01-15T10:00:00Z"
    completed_at: "2026-01-15T10:10:00Z"
    usage:
      input_tokens: 1500
      output_tokens: 500
      total_tokens: 2000
      tool_uses: 5
      duration_ms: 10000
  - step_id: run-phase-review
    phase: implement
    status: completed
    agent: reviewer
    started_at: "2026-01-15T10:10:00Z"
    completed_at: "2026-01-15T10:18:00Z"
    usage:
      input_tokens: 1200
      output_tokens: 300
      total_tokens: 1500
      tool_uses: 3
      duration_ms: 8000
  - step_id: run-phase-review
    phase: implement
    status: completed
    agent: reviewer
    started_at: "2026-01-15T10:18:00Z"
    completed_at: "2026-01-15T10:27:00Z"
    usage:
      input_tokens: 1400
      output_tokens: 400
      total_tokens: 1800
      tool_uses: 4
      duration_ms: 9000
  - step_id: mark-change-completed
    phase: complete
    status: completed
    agent: inline
    started_at: "2026-01-15T10:27:00Z"
    completed_at: "2026-01-15T10:27:05Z"
    usage:
      total_tokens: 0
      tool_uses: 2
      duration_ms: 5000
STATEYAML

# tasks.md for resolution metrics (avoid division by zero)
cat > "$STATE_DIR/tasks.md" <<'TASKSMD'
- [x] T-1: Task one
- [x] T-2: Task two
TASKSMD

OUTPUT=$(bash "$SCRIPT" "$STATE_DIR" 2>/dev/null)
EXIT_CODE=$?

check "script exits 0" "$([[ $EXIT_CODE -eq 0 ]] && echo 0 || echo 1)"

echo ""
echo "Script output:"
echo "$OUTPUT"
echo ""

# ── per_step block assertions ─────────────────────────────────────────────
check_contains "output contains per_step: key" "$OUTPUT" "per_step:"

# Check each step_id appears as a key in per_step
check_contains "per_step has execute-next-task entry" "$OUTPUT" "execute-next-task:"
check_contains "per_step has run-phase-review entry" "$OUTPUT" "run-phase-review:"
check_contains "per_step has mark-change-completed entry" "$OUTPUT" "mark-change-completed:"

# Check per-entry sub-fields exist
check_contains "per_step entries have total_tokens field" "$OUTPUT" "total_tokens:"
check_contains "per_step entries have tool_uses field" "$OUTPUT" "tool_uses:"
check_contains "per_step entries have duration_ms field" "$OUTPUT" "duration_ms:"
check_contains "per_step entries have executions field" "$OUTPUT" "executions:"

# ── T-13: Token sum assertion (within ±1% of metrics.tokens.total) ────────
# metrics.tokens.total from step_history = 2000 + 1500 + 1800 + 0 = 5300
# per_step totals: execute=2000 + run-phase-review=3300 + inline=0 = 5300

TOTAL_TOKENS=$(echo "$OUTPUT" | awk '/^  tokens:/{in_t=1} in_t && /^    total:/{gsub(/.*: */,""); print; exit}')
TOTAL_TOKENS=${TOTAL_TOKENS:-0}
echo "metrics.tokens.total = $TOTAL_TOKENS"

# Extract per_step total_tokens values (sum them)
# Per-step block uses "    total_tokens: N" indented under each step_id key
PER_STEP_SUM=$(echo "$OUTPUT" | awk '
  /^  per_step:/{in_ps=1; next}
  in_ps && /^      total_tokens:/{gsub(/.*: */,""); sum+=$0+0}
  in_ps && /^  [a-z]/ && !/^  per_step:/{in_ps=0}
  END{print sum+0}
')
PER_STEP_SUM=${PER_STEP_SUM:-0}
echo "sum(per_step[*].total_tokens) = $PER_STEP_SUM"

if [[ "$TOTAL_TOKENS" -gt 0 ]] && [[ "$PER_STEP_SUM" -ge 0 ]]; then
  # Compute ratio and check within ±1%
  RATIO=$(awk -v ps="$PER_STEP_SUM" -v t="$TOTAL_TOKENS" 'BEGIN {
    diff = ps - t; if (diff < 0) diff = -diff
    if (t == 0) { print "1.0"; exit }
    printf "%.6f", diff / t
  }')
  echo "Deviation ratio = $RATIO (must be <= 0.01)"
  [[ $(echo "$RATIO <= 0.01" | bc -l 2>/dev/null) -eq 1 ]]
  check "per_step token sum ($PER_STEP_SUM) equals metrics.tokens.total ($TOTAL_TOKENS) within 1%" $?
else
  check "per_step token sum is computable (both values present)" 1
fi

# Check executions count (run-phase-review was retried → executions=2)
REVIEW_EXECUTIONS=$(echo "$OUTPUT" | awk '
  /run-phase-review:/{in_entry=1; next}
  in_entry && /executions:/{gsub(/.*: */,""); print; exit}
  in_entry && /^    [a-z]/ && !/executions:/{in_entry=0}
')
echo "run-phase-review executions = $REVIEW_EXECUTIONS"
[[ "$REVIEW_EXECUTIONS" == "2" ]]
check "run-phase-review executions=2 (retry-inclusive count)" $?

echo ""
echo "Results: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
