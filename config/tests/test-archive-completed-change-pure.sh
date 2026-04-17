#!/usr/bin/env bash
# Test: archive-completed-change step is a pure move+commit
#
# AC-2, FR-2: The archive step must NOT write/mutate status, completed_at,
# or archive_path in state.yaml. These fields are written by mark-change-completed.
# The archive step only: creates archive dir, copies artifacts, commits, cleans up.
#
# This is a structural assertion on the YAML step contract.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STEP_FILE="$REPO_ROOT/config/steps/archive-completed-change.yaml"

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

echo "=== Test: archive-completed-change is a pure move+commit ==="

[[ -f "$STEP_FILE" ]]
check "step file exists" $?

if [[ ! -f "$STEP_FILE" ]]; then
  echo "Results: $pass passed, $fail failed"
  [[ "$fail" -eq 0 ]]; exit $?
fi

# The step MUST NOT contain instructions to set status/completed_at/archive_path.
# Specifically, the "Set `status: completed`" and "Set `completed_at:" instructions
# must be absent — these belong in mark-change-completed.
grep -q 'Set `status: completed`\|Set.*completed_at.*ISO\|Set.*archive_path.*spec/changes' "$STEP_FILE"
MUTATION_PRESENT=$?
# We want MUTATION_PRESENT to be non-zero (1 = not found = good)
[[ "$MUTATION_PRESENT" -ne 0 ]]
check "instruction does NOT contain state-mutation (Set status/completed_at/archive_path)" $?

# Must still contain archive directory creation
grep -qi 'archive.*dir\|create.*archive\|mkdir\|archive_root\|YYYY-MM-DD' "$STEP_FILE"
check "instruction includes archive directory creation" $?

# Must contain copy/copy artifacts
grep -qi 'copy.*artifact\|artifact.*copy\|artifacts.*from\|copy.*from' "$STEP_FILE"
check "instruction includes artifact copy step" $?

# Must contain commit step
grep -qi 'commit' "$STEP_FILE"
check "instruction includes commit step" $?

# Must contain cleanup step
grep -qi 'clean\|cleanup\|remove.*active\|active.*dir' "$STEP_FILE"
check "instruction includes cleanup step" $?

# Verify section must assert that status/metrics are present (checked, not written)
grep -qi 'status.*completed\|metrics' "$STEP_FILE"
check "verify section checks that status/metrics are already present" $?

echo ""
echo "Results: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
