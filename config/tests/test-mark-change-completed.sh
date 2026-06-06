#!/usr/bin/env bash
# Test: mark-change-completed step contract
#
# Case 1 (T-1): Step file exists and contains required field-write instructions
#   for status, completed_at, and archive_path.
# Case 2 (T-3): Step contains a field-presence validator that warns to stderr
#   about missing usage.duration_ms / usage.tool_uses and exits 0.
#
# Both cases are structural assertions on the YAML step contract — the step
# is LLM-executed at runtime, so we verify the contract content, not execution.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STEP_FILE="$REPO_ROOT/config/steps/mark-change-completed.yaml"

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

echo "=== Test: mark-change-completed step contract ==="

# ── Case 1: File must exist ───────────────────────────────────────────────
[[ -f "$STEP_FILE" ]]
check "step file exists at config/steps/mark-change-completed.yaml" $?

if [[ ! -f "$STEP_FILE" ]]; then
  echo ""
  echo "Results: $pass passed, $fail failed"
  [[ "$fail" -eq 0 ]]
  exit $?
fi

# ── Case 1: Required field writes ────────────────────────────────────────
# Step must instruct the agent to write: status: completed, completed_at, archive_path
grep -q 'status.*completed\|status: completed' "$STEP_FILE"
check "instruction includes status: completed write" $?

grep -q 'completed_at' "$STEP_FILE"
check "instruction includes completed_at write" $?

grep -q 'archive_path' "$STEP_FILE"
check "instruction includes archive_path write" $?

# Must be an inline step (no agent: field, or agent: inline)
# FR-1 specifies inline step
if grep -q '^agent:' "$STEP_FILE"; then
  agent_val=$(grep '^agent:' "$STEP_FILE" | awk '{print $2}')
  [[ "$agent_val" == "inline" ]] || [[ "$agent_val" == "~" ]]
  check "step is inline (agent: inline or no agent)" $?
else
  check "step is inline (no agent field)" 0
fi

# ── Case 2: Validator (T-3) ───────────────────────────────────────────────
# Step must contain validator logic: scan step_history for missing usage fields,
# emit stderr warning with coverage ratio, exit 0 (non-blocking).
grep -qi 'validator\|coverage ratio\|duration_ms.*tool_uses\|step_history.*missing\|missing.*usage' "$STEP_FILE"
check "instruction includes field-presence validator reference" $?

grep -qi 'stderr\|>&2\|warn' "$STEP_FILE"
check "instruction includes stderr warning" $?

grep -qi 'non.blocking\|exit 0\|always.*exit\|exit.*0' "$STEP_FILE"
check "instruction specifies non-blocking (exit 0) validator" $?

echo ""
echo "Results: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
