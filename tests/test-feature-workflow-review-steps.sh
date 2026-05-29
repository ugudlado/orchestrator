#!/usr/bin/env bash
# Test: feature.yaml implement phase has exactly one reviewer spawn
#
# FR-5, AC-5: The feature.yaml implement phase must:
#   - Contain exactly one 'run-implement-review' entry
#   - Contain zero 'run-simplify' entries
#   - Contain zero 'run-feature-verification' entries
#   - Keep 'run-ux-critique' as a conditional step (if ux_design)
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
FEATURE_YAML="$REPO_ROOT/config/workflows/feature.yaml"

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

echo "=== Test: feature.yaml implement phase reviewer steps ==="

[[ -f "$FEATURE_YAML" ]]
check "feature.yaml exists" $?
if [[ ! -f "$FEATURE_YAML" ]]; then
  echo "Results: $pass passed, $fail failed"
  [[ "$fail" -eq 0 ]]; exit $?
fi

# Extract only the implement phase steps section
# The implement phase is between 'name: implement' and the next '  - name:' or end
IMPLEMENT_SECTION=$(awk '
  /^  - name: implement/ { in_impl=1; next }
  in_impl && /^  - (name:|include:)/ { in_impl=0 }
  in_impl { print }
' "$FEATURE_YAML")

echo "Implement phase content:"
echo "$IMPLEMENT_SECTION"
echo ""

# Count occurrences of each reviewer step
COUNT_IMPLEMENT_REVIEW=$(echo "$IMPLEMENT_SECTION" | grep -c 'run-implement-review' || true)
COUNT_SIMPLIFY=$(echo "$IMPLEMENT_SECTION" | grep -c 'run-simplify' || true)
COUNT_VERIFICATION=$(echo "$IMPLEMENT_SECTION" | grep -c 'run-feature-verification' || true)
COUNT_UX=$(echo "$IMPLEMENT_SECTION" | grep -c 'run-ux-critique' || true)

echo "run-implement-review count: $COUNT_IMPLEMENT_REVIEW"
echo "run-simplify count: $COUNT_SIMPLIFY"
echo "run-feature-verification count: $COUNT_VERIFICATION"
echo "run-ux-critique count: $COUNT_UX"

# Assertions
[[ "$COUNT_IMPLEMENT_REVIEW" -eq 1 ]]
check "implement phase contains exactly one run-implement-review" $?

[[ "$COUNT_SIMPLIFY" -eq 0 ]]
check "implement phase contains zero run-simplify entries" $?

[[ "$COUNT_VERIFICATION" -eq 0 ]]
check "implement phase contains zero run-feature-verification entries" $?

# run-ux-critique must be conditional (should contain 'if ux_design')
UX_CONDITIONAL=$(echo "$IMPLEMENT_SECTION" | grep 'run-ux-critique' | grep -c 'if ux_design' || true)
[[ "$COUNT_UX" -ge 1 ]]
check "implement phase contains run-ux-critique step" $?

[[ "$UX_CONDITIONAL" -ge 1 ]]
check "run-ux-critique is conditional on ux_design flag" $?

echo ""
echo "Results: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
