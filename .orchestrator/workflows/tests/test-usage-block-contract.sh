#!/usr/bin/env bash
# Test: usage-block contract enforcement
#
# FR-8, AC-8: Every step_history entry must carry a usage: block with at least
# duration_ms and tool_uses fields. Inline steps use agent: inline and omit
# token fields (treated as 0).
#
# This test:
# 1. Verifies the skill/orchestrate/SKILL.md documents the inline-step usage schema.
# 2. Parses a fixture state.yaml to detect entries missing required usage fields.
# 3. Confirms the test correctly detects missing-field gaps.
# (CONVENTIONS.md check removed — usage contract lives in SKILL.md only)
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SKILL_FILE="$REPO_ROOT/skills/archive/orchestrate/SKILL.md"

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
  local file="$2"
  local needle="$3"
  if grep -q "$needle" "$file" 2>/dev/null; then
    echo "PASS: $desc"
    ((pass++))
  else
    echo "FAIL: $desc — '$needle' not found in $file"
    ((fail++))
  fi
}

echo "=== Test: usage-block contract ==="

# ── Part 1: SKILL.md documents inline-step usage schema ──────────────────
[[ -f "$SKILL_FILE" ]]
check "SKILL.md exists" $?

# FR-8, FR-10, AC-11: The skill must document that inline steps record duration_ms
# and tool_uses with agent: inline marker
check_contains "SKILL.md documents inline step usage schema" "$SKILL_FILE" "inline"
check_contains "SKILL.md documents duration_ms for inline steps" "$SKILL_FILE" "duration_ms"
check_contains "SKILL.md documents tool_uses for inline steps" "$SKILL_FILE" "tool_uses"
check_contains "SKILL.md documents agent: inline marker" "$SKILL_FILE" "agent: inline\|agent.*inline"

# ── Part 2: Fixture validation — detect missing usage fields ─────────────
TMPDIR_BASE="${TMPDIR:-/tmp}/test-usage-block-$$"
mkdir -p "$TMPDIR_BASE"
cleanup() { rm -rf "$TMPDIR_BASE"; }
trap cleanup EXIT

# Fixture: 4 entries, 2 have full usage, 1 has only partial, 1 is missing usage
cat > "$TMPDIR_BASE/state.yaml" <<'STATEYAML'
change_id: test-usage-block-001
schema: feature
status: completed
step_history:
  - step_id: load-project-context
    phase: specify
    status: completed
    agent: developer
    started_at: "2026-01-15T10:00:00Z"
    completed_at: "2026-01-15T10:05:00Z"
    usage:
      total_tokens: 1000
      tool_uses: 3
      duration_ms: 5000
  - step_id: execute-next-task
    phase: implement
    status: completed
    agent: developer
    started_at: "2026-01-15T10:05:00Z"
    completed_at: "2026-01-15T10:15:00Z"
    usage:
      total_tokens: 2000
      tool_uses: 8
      duration_ms: 10000
  - step_id: mark-change-completed
    phase: complete
    status: completed
    agent: inline
    started_at: "2026-01-15T10:15:00Z"
    completed_at: "2026-01-15T10:15:05Z"
    usage:
      tool_uses: 1
      duration_ms: 5000
  - step_id: archive-completed-change
    phase: complete
    status: completed
    agent: developer
    started_at: "2026-01-15T10:15:05Z"
    completed_at: "2026-01-15T10:16:00Z"
STATEYAML

# Count entries and detect missing usage fields using awk
COVERAGE=$(awk '
  /^step_history:/{in_h=1; next}
  in_h && /^[a-z]/{in_h=0}
  in_h && /^  - step_id:/{
    if (in_entry) {
      total++
      if (has_duration && has_tool_uses) covered++
    }
    in_entry=1; has_duration=0; has_tool_uses=0; in_usage=0
  }
  in_h && in_entry && /^    usage:/{in_usage=1; next}
  in_h && in_entry && in_usage && /^      duration_ms:/{has_duration=1}
  in_h && in_entry && in_usage && /^      tool_uses:/{has_tool_uses=1}
  in_h && in_entry && in_usage && /^    [a-z]/{in_usage=0}
  END {
    if (in_entry) {
      total++
      if (has_duration && has_tool_uses) covered++
    }
    printf "%d/%d", covered, total
  }
' "$TMPDIR_BASE/state.yaml")

echo ""
echo "Coverage ratio from fixture: $COVERAGE"

COVERED=$(echo "$COVERAGE" | cut -d/ -f1)
TOTAL=$(echo "$COVERAGE" | cut -d/ -f2)

[[ "$TOTAL" -eq 4 ]]
check "fixture has 4 step_history entries" $?

[[ "$COVERED" -eq 3 ]]
check "fixture has 3 fully-covered entries (1 missing duration_ms and tool_uses)" $?

# Coverage should be less than total (test correctly detects the gap)
[[ "$COVERED" -lt "$TOTAL" ]]
check "gap detector correctly identifies incomplete entries (covered=$COVERED < total=$TOTAL)" $?

echo ""
echo "Results: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
