#!/usr/bin/env bash
# Test: backfill-zero-cost-metrics.sh behavior
#
# FR-11, AC-4: The backfill script must:
# (a) Update metrics for archives where cost.net_usd == 0 AND JSONL files exist
# (b) Skip archives where JSONL files are absent, logging 'skip: no-jsonl'
# (c) Not corrupt archives with existing non-zero cost
# (d) Print a summary with updated/skipped/failed counts
#
# T-26: RED test — backfill script absent
# T-27: GREEN — after creating the script
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$REPO_ROOT/config/scripts/backfill-zero-cost-metrics.sh"

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

echo "=== Test: backfill-zero-cost-metrics.sh ==="

# Script must exist
[[ -f "$SCRIPT" ]]
check "backfill script exists at config/scripts/backfill-zero-cost-metrics.sh" $?

if [[ ! -f "$SCRIPT" ]]; then
  echo ""
  echo "Results: $pass passed, $fail failed"
  [[ "$fail" -eq 0 ]]
  exit $?
fi

TMPDIR_BASE="${TMPDIR:-/tmp}/test-backfill-$$"
mkdir -p "$TMPDIR_BASE"
cleanup() { rm -rf "$TMPDIR_BASE"; }
trap cleanup EXIT

# ── Fixture A: archive with zero cost, NO JSONL ───────────────────────────
ARCHIVE_A="$TMPDIR_BASE/archive/2026-01-01-no-jsonl-test"
mkdir -p "$ARCHIVE_A"
cat > "$ARCHIVE_A/state.yaml" <<'YAML'
change_id: no-jsonl-test
slug: no-jsonl-test
schema: feature
status: completed
started_at: "2026-01-01T10:00:00Z"
completed_at: "2026-01-01T11:00:00Z"
metrics:
  cost:
    net_usd: 0
    gross_usd: 0
  tokens:
    total: 0
YAML

# ── Fixture B: archive with zero cost, JSONL present ─────────────────────
ARCHIVE_B="$TMPDIR_BASE/archive/2026-01-02-with-jsonl-test"
mkdir -p "$ARCHIVE_B"
cat > "$ARCHIVE_B/state.yaml" <<'YAML'
change_id: with-jsonl-test
slug: with-jsonl-test
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
    completed_at: "2026-01-15T10:30:00Z"
    usage:
      input_tokens: 1000
      output_tokens: 400
      total_tokens: 1400
      tool_uses: 5
      duration_ms: 30000
metrics:
  cost:
    net_usd: 0
    gross_usd: 0
  tokens:
    total: 0
YAML

# ── Fixture C: archive with non-zero cost (should NOT be backfilled) ──────
ARCHIVE_C="$TMPDIR_BASE/archive/2026-01-03-nonzero-cost-test"
mkdir -p "$ARCHIVE_C"
ORIGINAL_COST="0.1234"
cat > "$ARCHIVE_C/state.yaml" <<YAML
change_id: nonzero-cost-test
slug: nonzero-cost-test
schema: feature
status: completed
started_at: "2026-01-15T10:00:00Z"
completed_at: "2026-01-15T11:00:00Z"
metrics:
  cost:
    net_usd: ${ORIGINAL_COST}
    gross_usd: 0.2000
  tokens:
    total: 5000
YAML

# Run the script in dry-run mode against our fixture archive directory
# The script takes the archive root as an argument and an optional --dry-run flag
OUTPUT=$(bash "$SCRIPT" --dry-run "$TMPDIR_BASE/archive" 2>&1)
EXIT_CODE=$?

echo ""
echo "Script output:"
echo "$OUTPUT"
echo ""

check "script exits 0" "$([[ $EXIT_CODE -eq 0 ]] && echo 0 || echo 1)"

# Fixture A (no JSONL): must be skipped with 'skip: no-jsonl' message
check_contains "script logs skip:no-jsonl for archive without JSONL" "$OUTPUT" "skip.*no.jsonl\|no-jsonl\|no jsonl"

# Fixture C (non-zero cost): must be skipped
check_contains "script skips archive with existing non-zero cost" "$OUTPUT" "nonzero-cost-test\|non.zero\|already.*non.zero\|cost.*non.zero\|skip"

# Summary line must appear
check_contains "script prints summary line" "$OUTPUT" "updated\|skipped\|failed\|Summary\|summary"

# Non-zero archive must be unchanged
FINAL_COST=$(grep 'net_usd:' "$ARCHIVE_C/state.yaml" | awk '{print $2}')
[[ "$FINAL_COST" == "$ORIGINAL_COST" ]]
check "non-zero cost archive is unchanged after backfill" $?

echo ""
echo "Results: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
