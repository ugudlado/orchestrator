#!/usr/bin/env bash
# Test: execute-next-task.yaml contains a simplify pass for the last task
#
# FR-6, AC-6: execute-next-task must have an appended instruction block that:
# - Runs AFTER the final task completes (gated on "last task" / "all tasks complete")
# - Instructs the developer agent to run a simplify pass over changed files
# - Does NOT introduce a new agent spawn (same developer agent)
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STEP_FILE="$REPO_ROOT/config/steps/execute-next-task.yaml"

pass=0
fail=0

check() {
  local desc="$1"
  local result="$2"
  if [[ "$result" -eq 0 ]]; then
    echo "PASS: $desc"
    ((pass++))
  else
    echo "FAIL: $desc"
    ((fail++))
  fi
}

echo "=== Test: execute-next-task simplify pass ==="

[[ -f "$STEP_FILE" ]]
check "execute-next-task.yaml exists" $?
if [[ ! -f "$STEP_FILE" ]]; then
  echo "Results: $pass passed, $fail failed"
  [[ "$fail" -eq 0 ]]; exit $?
fi

# The step must contain a simplify pass instruction gated on the last task
# Key signals: "simplify", "last task" or "all tasks" or "no unchecked", not a new agent spawn
grep -qi 'simplify' "$STEP_FILE"
check "instruction contains 'simplify' reference" $?

grep -qi 'last task\|all tasks.*complet\|no.*unchecked\|no unchecked\|after.*last\|final task' "$STEP_FILE"
check "simplify pass is gated on last task completion" $?

# The simplify pass must be within the same developer agent — no new spawn
# A new spawn would require a new 'agent:' field on a step entry or explicit spawning
# Check that the instruction does NOT add a new step spawn for simplify
SIMPLIFY_SECTION=$(grep -A 20 -i 'simplify' "$STEP_FILE" | head -30)

echo ""
echo "Simplify-related content in step:"
echo "$SIMPLIFY_SECTION"
echo ""

# The simplify pass should be an instruction block, not a new step reference
# Negative check: should NOT have a separate spawn keyword adjacent to simplify
echo "$SIMPLIFY_SECTION" | grep -qi 'run-simplify\|new.*agent.*spawn\|spawn.*new.*agent'
NOT_NEW_SPAWN=$?
[[ "$NOT_NEW_SPAWN" -ne 0 ]]
check "simplify pass does not introduce a new agent spawn" $?

# Check that the step still has the main developer agent
grep -q '^agent: developer' "$STEP_FILE"
check "step agent is still developer (no second agent block added)" $?

echo ""
echo "Results: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
