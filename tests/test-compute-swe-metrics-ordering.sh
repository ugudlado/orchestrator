#!/usr/bin/env bash
# Test: compute-swe-metrics.sh produces non-zero tokens when completed_at is set
# and matching JSONL fixture files exist in the expected project directory.
#
# AC-3, FR-3: Given state.yaml with completed_at set and JSONL files present,
# compute-swe-metrics.sh must emit cost.net_usd > 0, tokens.input > 0,
# tokens.output > 0.
#
# The JSONL fixture contains synthetic assistant turns with known token counts
# within the time window defined by started_at/completed_at.
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

check_gt() {
  local desc="$1"
  local val="$2"
  local threshold="$3"
  if (( $(echo "$val > $threshold" | bc -l 2>/dev/null || echo 0) )); then
    echo "PASS: $desc (value=$val > $threshold)"
    ((pass++))
  else
    echo "FAIL: $desc (value=$val, expected > $threshold)"
    ((fail++))
  fi
}

echo "=== Test: compute-swe-metrics with completed_at and JSONL fixture ==="

[[ -f "$SCRIPT" ]]
check "compute-swe-metrics.sh exists" $?

if [[ ! -f "$SCRIPT" ]]; then
  echo "Results: $pass passed, $fail failed"
  [[ "$fail" -eq 0 ]]; exit $?
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "SKIP: jq not available, skipping JSONL test"
  echo "Results: $pass passed, $fail failed"
  exit 0
fi

# ── Create temp fixture ───────────────────────────────────────────────────
TMPDIR_BASE="${TMPDIR:-/tmp}/test-metrics-ordering-$$"
mkdir -p "$TMPDIR_BASE"
cleanup() { rm -rf "$TMPDIR_BASE"; }
trap cleanup EXIT

STATE_DIR="$TMPDIR_BASE/state"
mkdir -p "$STATE_DIR"

# Times: started 1h before completed
STARTED="2026-01-15T10:00:00Z"
COMPLETED="2026-01-15T11:00:00Z"
# JSONL entry time: within the window
JSONL_TIME="2026-01-15T10:30:00.000Z"

# state.yaml: feature schema with completed_at set
cat > "$STATE_DIR/state.yaml" <<STATEYAML
change_id: test-metrics-ordering-001
slug: test-metrics-ordering-001
schema: feature
status: completed
started_at: "$STARTED"
completed_at: "$COMPLETED"
step_history:
  - step_id: execute-next-task
    phase: implement
    status: completed
    agent: developer
    started_at: "${STARTED}"
    completed_at: "${COMPLETED}"
    usage:
      input_tokens: 1000
      output_tokens: 500
      total_tokens: 1500
      tool_uses: 5
      duration_ms: 60000
STATEYAML

# Fake JSONL fixture: synthetic assistant turn within the time window
# The slug maps to the git repo root using path replacement (/ -> -)
# We override HOME so the script's slug lookup points to our fixture.
FAKE_REPO_PATH="$TMPDIR_BASE/fakerepo"
mkdir -p "$FAKE_REPO_PATH"
# Fake git repo (compute-swe-metrics uses `git rev-parse --show-toplevel`)
git -C "$FAKE_REPO_PATH" init --quiet 2>/dev/null || true
git -C "$FAKE_REPO_PATH" commit --allow-empty -m "init" --quiet 2>/dev/null || true

# Compute slug: path -> replace / with -
FAKE_SLUG="${FAKE_REPO_PATH//\//-}"
FAKE_PROJECT_DIR="$TMPDIR_BASE/home/.claude/projects/$FAKE_SLUG"
mkdir -p "$FAKE_PROJECT_DIR"

# Write JSONL with 3000 input, 1200 output tokens
cat > "$FAKE_PROJECT_DIR/session-001.jsonl" <<JSONL
{"type":"assistant","timestamp":"${JSONL_TIME}","message":{"role":"assistant","model":"claude-sonnet-4-5","usage":{"input_tokens":3000,"output_tokens":1200,"cache_creation_input_tokens":0,"cache_read_input_tokens":0},"content":[]}}
JSONL

# Run the script with the fake HOME so it finds our JSONL fixture
# We need to cd into the fake repo so git rev-parse works
OUTPUT=$(cd "$FAKE_REPO_PATH" && HOME="$TMPDIR_BASE/home" bash "$SCRIPT" "$STATE_DIR" 2>/dev/null)
EXIT_CODE=$?

check "script exits 0" "$([[ $EXIT_CODE -eq 0 ]] && echo 0 || echo 1)"

echo ""
echo "Script output (first 30 lines):"
echo "$OUTPUT" | head -30

# Parse token values from YAML output
INPUT_TOKENS=$(echo "$OUTPUT" | awk '/^  tokens:/{in_t=1} in_t && /^    input:/{gsub(/.*: */,""); print; exit}')
OUTPUT_TOKENS=$(echo "$OUTPUT" | awk '/^  tokens:/{in_t=1} in_t && /^    output:/{gsub(/.*: */,""); print; exit}')
NET_USD=$(echo "$OUTPUT" | awk '/^  cost:/{in_c=1} in_c && /^    net_usd:/{gsub(/.*: */,""); print; exit}')

INPUT_TOKENS=${INPUT_TOKENS:-0}
OUTPUT_TOKENS=${OUTPUT_TOKENS:-0}
NET_USD=${NET_USD:-0}

echo ""
echo "Parsed: input_tokens=$INPUT_TOKENS output_tokens=$OUTPUT_TOKENS net_usd=$NET_USD"

check_gt "tokens.input > 0" "${INPUT_TOKENS:-0}" "0"
check_gt "tokens.output > 0" "${OUTPUT_TOKENS:-0}" "0"
check_gt "cost.net_usd > 0" "${NET_USD:-0}" "0"

echo ""
echo "Results: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
