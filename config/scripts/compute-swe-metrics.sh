#!/bin/bash
# compute-swe-metrics.sh — Extract SWE-bench-aligned metrics from state.yaml + git history.
#
# Usage: compute-swe-metrics.sh <state_dir>
#
# Reads:  state.yaml (step_history[].usage for tokens/cost, step_history for resolution)
#         git log (for churn metrics)
# Writes: metrics block to stdout as YAML (for injection into state.yaml)
#
# Token/cost data comes from step_history[].usage, written by the orchestrator
# dispatch loop after each agent step. No transcript parsing needed.
#
# Methodology references:
#   - Token counting: sum step_history[].usage fields (input_tokens, output_tokens, etc.)
#   - Cost: gross = no cache discount, net = with cache discount
#   - Resolution: binary per-task (F2P=1 AND P2P=1 maps to task acceptance + no regressions)
#   - pass@k: Aider methodology (k=1 first attempt, k=2 within two attempts)

set -uo pipefail

STATE_DIR="${1:?Usage: compute-swe-metrics.sh <state_dir>}"

STATE_FILE="$STATE_DIR/state.yaml"
if [[ ! -f "$STATE_FILE" ]]; then
  echo "ERROR: state.yaml not found at $STATE_FILE" >&2
  exit 1
fi

# ── Model Pricing ($/1M tokens) ──────────────────────────────────────────
# Updated as of 2026-04. Add new models as needed.
get_pricing() {
  local model="$1"
  case "$model" in
    claude-opus-4-6*|claude-opus-4-5*)
      echo "15.00 75.00 1.50" ;;  # input output cache_read
    claude-sonnet-4-6*|claude-sonnet-4-5*)
      echo "3.00 15.00 0.30" ;;
    claude-haiku-4-5*)
      echo "0.80 4.00 0.08" ;;
    *)
      echo "15.00 75.00 1.50" ;;  # default to opus pricing (conservative)
  esac
}

# ── Token Counting (from state.yaml step_history) ───────────────────────
# Primary source: step_history[].usage written by the orchestrator after each agent step.
# Each entry has: input_tokens, output_tokens, cache_creation_input_tokens,
# cache_read_input_tokens, total_tokens, tool_uses, duration_ms.
TOTAL_INPUT=0
TOTAL_OUTPUT=0
TOTAL_CACHE_CREATION=0
TOTAL_CACHE_READ=0
TOTAL_TOKENS=0
TOTAL_TOOL_CALLS=0
TOTAL_TURNS=0
MODEL="unknown"

STATE_USAGE=$(awk '
  /^step_history:/ { in_sh=1; next }
  in_sh && /^[^ ]/ && !/^  / { in_sh=0 }
  in_sh && /^  - / { flush() }
  in_sh && /^    usage:/ { in_u=1; next }
  in_sh && in_u && /^      input_tokens:/                 { gsub(/.*: */, ""); inp+=$0+0 }
  in_sh && in_u && /^      output_tokens:/                { gsub(/.*: */, ""); out+=$0+0 }
  in_sh && in_u && /^      cache_creation_input_tokens:/  { gsub(/.*: */, ""); cc+=$0+0 }
  in_sh && in_u && /^      cache_read_input_tokens:/      { gsub(/.*: */, ""); cr+=$0+0 }
  in_sh && in_u && /^      total_tokens:/                 { gsub(/.*: */, ""); t+=$0+0 }
  in_sh && in_u && /^      tool_uses:/                    { gsub(/.*: */, ""); u+=$0+0 }
  in_sh && in_u && /^      duration_ms:/                  { gsub(/.*: */, ""); d+=$0+0 }
  in_sh && in_u && /^    [a-z]/ && !/^      / { in_u=0 }
  function flush() { if (in_u) in_u=0; s++ }
  END { flush(); printf "%d %d %d %d %d %d %d %d", inp+0, out+0, cc+0, cr+0, t+0, u+0, d+0, s+0 }
' "$STATE_FILE")

TOTAL_INPUT=$(echo "$STATE_USAGE" | awk '{print $1}')
TOTAL_OUTPUT=$(echo "$STATE_USAGE" | awk '{print $2}')
TOTAL_CACHE_CREATION=$(echo "$STATE_USAGE" | awk '{print $3}')
TOTAL_CACHE_READ=$(echo "$STATE_USAGE" | awk '{print $4}')
TOTAL_TOKENS=$(echo "$STATE_USAGE" | awk '{print $5}')
TOTAL_TOOL_CALLS=$(echo "$STATE_USAGE" | awk '{print $6}')
TOTAL_DURATION_MS=$(echo "$STATE_USAGE" | awk '{print $7}')
TOTAL_STEPS=$(echo "$STATE_USAGE" | awk '{print $8}')

# If total_tokens is 0 but we have input+output, compute it
if [[ "$TOTAL_TOKENS" -eq 0 && "$TOTAL_INPUT" -gt 0 ]]; then
  TOTAL_TOKENS=$((TOTAL_INPUT + TOTAL_OUTPUT + TOTAL_CACHE_CREATION))
fi

# ── Cost Calculation ─────────────────────────────────────────────────────
PRICING=$(get_pricing "$MODEL")
INPUT_PRICE=$(echo "$PRICING" | awk '{print $1}')
OUTPUT_PRICE=$(echo "$PRICING" | awk '{print $2}')
CACHE_READ_PRICE=$(echo "$PRICING" | awk '{print $3}')

# gross_usd: SWE-bench HAL style — ALL tokens at full price, no cache discount.
GROSS_USD=$(echo "scale=4; ($TOTAL_INPUT + $TOTAL_CACHE_CREATION + $TOTAL_CACHE_READ) * $INPUT_PRICE / 1000000 + $TOTAL_OUTPUT * $OUTPUT_PRICE / 1000000" | bc)

# net_usd: Actual billed cost with cache discounts applied.
NET_USD=$(echo "scale=4; ($TOTAL_INPUT + $TOTAL_CACHE_CREATION) * $INPUT_PRICE / 1000000 + $TOTAL_CACHE_READ * $CACHE_READ_PRICE / 1000000 + $TOTAL_OUTPUT * $OUTPUT_PRICE / 1000000" | bc)

# ── Wall Clock ───────────────────────────────────────────────────────────
STARTED_AT=$(grep '^started_at:' "$STATE_FILE" | head -1 | sed 's/^started_at: *//' | tr -d '"')
COMPLETED_AT=$(grep '^completed_at:' "$STATE_FILE" | head -1 | sed 's/^completed_at: *//' | tr -d '"')

WALL_CLOCK=0
if [[ -n "$STARTED_AT" && -n "$COMPLETED_AT" ]]; then
  # Try multiple date formats (macOS and Linux)
  START_EPOCH=$(date -j -f "%Y-%m-%dT%H:%M:%SZ" "$STARTED_AT" "+%s" 2>/dev/null \
    || date -j -f "%Y-%m-%dT%H:%M:%S" "${STARTED_AT%Z}" "+%s" 2>/dev/null \
    || date -d "$STARTED_AT" "+%s" 2>/dev/null \
    || echo 0)
  END_EPOCH=$(date -j -f "%Y-%m-%dT%H:%M:%SZ" "$COMPLETED_AT" "+%s" 2>/dev/null \
    || date -j -f "%Y-%m-%dT%H:%M:%S" "${COMPLETED_AT%Z}" "+%s" 2>/dev/null \
    || date -d "$COMPLETED_AT" "+%s" 2>/dev/null \
    || echo 0)
  if [[ "$START_EPOCH" -gt 0 && "$END_EPOCH" -gt 0 ]]; then
    WALL_CLOCK=$(echo "scale=1; ($END_EPOCH - $START_EPOCH) / 60" | bc)
  fi
fi

# ── Git Churn ────────────────────────────────────────────────────────────
CHANGE_ID=$(grep -E '^(change_id|linear_ticket_id|linear_ticket):' "$STATE_FILE" | head -1 | awk '{print $2}' | tr -d '"')
SLUG=$(grep '^slug:' "$STATE_FILE" 2>/dev/null | awk '{print $2}' | tr -d '"')
FEATURE_BRANCH="feature/${SLUG:-$CHANGE_ID}"

FILES_CHANGED=0
INSERTIONS=0
DELETIONS=0
TOTAL_COMMITS=0
REWORK_COMMITS=0

SEARCH_TERM="${SLUG:-$CHANGE_ID}"
if [[ -n "$SEARCH_TERM" ]]; then
  # Search commit messages for the feature slug (preferred) or Linear ticket ID
  GREP_PATTERN="$SEARCH_TERM"
  # Also include ticket ID if different from slug
  [[ -n "$CHANGE_ID" && "$CHANGE_ID" != "$SEARCH_TERM" ]] && GREP_PATTERN="${SEARCH_TERM}\|${CHANGE_ID}"

  COMMIT_HASHES=$(git log --grep="$GREP_PATTERN" --no-merges --format="%H" 2>/dev/null || true)
  if [[ -n "$COMMIT_HASHES" ]]; then
    TOTAL_COMMITS=$(echo "$COMMIT_HASHES" | wc -l | tr -d ' ')
    REWORK_COMMITS=$(git log --grep="$GREP_PATTERN" --no-merges --format="%s" 2>/dev/null | grep -c "^fix:" || true)
    REWORK_COMMITS=${REWORK_COMMITS:-0}

    # Unique files changed across the feature's commits only
    FIRST_COMMIT=$(echo "$COMMIT_HASHES" | tail -1)
    LAST_COMMIT=$(echo "$COMMIT_HASHES" | head -1)
    FILES_CHANGED=$(git diff --name-only "${FIRST_COMMIT}^".."$LAST_COMMIT" -- 2>/dev/null | sort -u | wc -l | tr -d ' ')
    # Aggregate insertions/deletions via combined diff
    DIFF_STAT=$(git diff --stat "${FIRST_COMMIT}^".."$LAST_COMMIT" -- 2>/dev/null | tail -1 || true)
    if [[ -n "$DIFF_STAT" ]]; then
      INSERTIONS=$(echo "$DIFF_STAT" | grep -o '[0-9]* insertion' | awk '{print $1}')
      DELETIONS=$(echo "$DIFF_STAT" | grep -o '[0-9]* deletion' | awk '{print $1}')
    fi
    INSERTIONS=${INSERTIONS:-0}
    DELETIONS=${DELETIONS:-0}
  fi
fi

# ── Task Resolution (from state.yaml) ────────────────────────────────────
# Parse verification_results for task pass/fail/retry counts
TASKS_FILE="$STATE_DIR/tasks.md"
TASKS_TOTAL=0
TASKS_COMPLETED=0
TASKS_FAILED=0
FIRST_ATTEMPT_PASS=0

if [[ -f "$TASKS_FILE" ]]; then
  # Count checked tasks [x] and unchecked [ ]
  TASKS_TOTAL=$(grep -cE '^\s*-\s*\[' "$TASKS_FILE" 2>/dev/null || echo 0)
  TASKS_COMPLETED=$(grep -cE '^\s*-\s*\[x\]' "$TASKS_FILE" 2>/dev/null || echo 0)
  TASKS_FAILED=$((TASKS_TOTAL - TASKS_COMPLETED))
fi

# Parse verification_results from state.yaml for retry info
TASK_RETRIES=$(grep -A2 'task_T' "$STATE_FILE" 2>/dev/null | grep 'retries:' | awk '{print $2}' || true)
if [[ -n "$TASK_RETRIES" ]]; then
  FIRST_ATTEMPT_PASS=$(echo "$TASK_RETRIES" | awk '$1==0{c++} END{print c+0}')
fi

# Fallback: if no task data, use step history
if [[ "$TASKS_TOTAL" -eq 0 ]]; then
  # Count execute-next-task entries in step_history
  TASKS_TOTAL=$(grep -c 'step_id: execute-next-task' "$STATE_FILE" 2>/dev/null || echo 0)
  TASKS_COMPLETED=$TASKS_TOTAL  # If we got here, they all completed
  FIRST_ATTEMPT_PASS=$TASKS_TOTAL
fi

[[ "$TASKS_TOTAL" -eq 0 ]] && TASKS_TOTAL=1  # Avoid division by zero

RESOLVE_RATE=$(echo "scale=4; $TASKS_COMPLETED / $TASKS_TOTAL" | bc)
PASS_AT_1=$(echo "scale=4; $FIRST_ATTEMPT_PASS / $TASKS_TOTAL" | bc)

# pass@2: tasks that succeeded within 2 attempts
PASS_AT_2_COUNT=$TASKS_COMPLETED  # All completed tasks passed within some number of attempts
if [[ -n "$TASK_RETRIES" ]]; then
  PASS_AT_2_COUNT=$(echo "$TASK_RETRIES" | awk '$1<=1{c++} END{print c+0}')
fi
PASS_AT_2=$(echo "scale=4; $PASS_AT_2_COUNT / $TASKS_TOTAL" | bc)

# ── Retries ──────────────────────────────────────────────────────────────
RETRY_TOTAL=$(grep 'retries:' "$STATE_FILE" 2>/dev/null | awk '{s+=$2} END {print s+0}')

REWORK_RATE=0
if [[ "$TOTAL_COMMITS" -gt 0 ]]; then
  REWORK_RATE=$(echo "scale=4; $REWORK_COMMITS / $TOTAL_COMMITS" | bc)
fi

# ── Review Scores ────────────────────────────────────────────────────────
# Extract review scores: look for "overall: N" under review_score blocks
REVIEW_SCORES=$(grep -A1 'review_score:' "$STATE_FILE" 2>/dev/null | grep 'overall:' | awk '{print $2}' | tr '\n' ',' | sed 's/,$//')
SCORE_AVG=0
if [[ -n "$REVIEW_SCORES" ]]; then
  SCORE_AVG=$(echo "$REVIEW_SCORES" | tr ',' '\n' | awk '{s+=$1; c++} END {if(c>0) printf "%.1f", s/c; else print 0}')
fi

# ── Derived Benchmarks ───────────────────────────────────────────────────
COST_PER_TASK=$(echo "scale=4; $NET_USD / $TASKS_TOTAL" | bc)
COST_PER_RESOLUTION=0
[[ "$TASKS_COMPLETED" -gt 0 ]] && COST_PER_RESOLUTION=$(echo "scale=4; $NET_USD / $TASKS_COMPLETED" | bc)
TOKENS_PER_TASK=$((TOTAL_TOKENS / TASKS_TOTAL))
TOKENS_PER_RESOLUTION=0
[[ "$TASKS_COMPLETED" -gt 0 ]] && TOKENS_PER_RESOLUTION=$((TOTAL_TOKENS / TASKS_COMPLETED))

IO_RATIO=0
[[ "$TOTAL_OUTPUT" -gt 0 ]] && IO_RATIO=$(echo "scale=1; ($TOTAL_INPUT + $TOTAL_CACHE_CREATION) / $TOTAL_OUTPUT" | bc)

CACHE_DENOM=$((TOTAL_INPUT + TOTAL_CACHE_CREATION + TOTAL_CACHE_READ))
CACHE_HIT_RATE=0
[[ "$CACHE_DENOM" -gt 0 ]] && CACHE_HIT_RATE=$(echo "scale=4; $TOTAL_CACHE_READ / $CACHE_DENOM" | bc)

# ── Per-Agent Token Attribution ──────────────────────────────────────────
# Parse step_history entries that have both agent: and usage: sub-fields.
# Uses awk to walk the YAML structure without jq (state.yaml is YAML, not JSON).
# Entries without a usage: block are silently skipped (backward compatible).
PER_AGENT_TOKENS=$(awk '
  /^step_history:/ { in_history=1; next }
  in_history && /^[^ ]/ && !/^  / { in_history=0 }
  function flush_entry() {
    if (agent != "" && total_tokens > 0) {
      tok[agent]  += total_tokens
      uses[agent] += tool_uses
      dur[agent]  += duration_ms
      cnt[agent]  += 1
    }
    agent=""; total_tokens=0; tool_uses=0; duration_ms=0; in_usage=0
  }
  in_history && /^  - / { flush_entry() }
  in_history && /^    agent:/ { gsub(/.*agent: */, ""); gsub(/"/, ""); agent=$0 }
  in_history && /^    usage:/ { in_usage=1 }
  in_history && in_usage && /^      total_tokens:/ { gsub(/.*total_tokens: */, ""); total_tokens=$0+0 }
  in_history && in_usage && /^      tool_uses:/    { gsub(/.*tool_uses: */, "");    tool_uses=$0+0 }
  in_history && in_usage && /^      duration_ms:/  { gsub(/.*duration_ms: */, "");  duration_ms=$0+0 }
  in_history && in_usage && /^    [a-z]/ && !/^    usage:/ { in_usage=0 }
  END {
    flush_entry()
    sep=""
    printf "{"
    for (a in tok) {
      printf "%s\"%s\":{\"total_tokens\":%d,\"tool_uses\":%d,\"duration_ms\":%d,\"steps\":%d}",
        sep, a, tok[a], uses[a], dur[a], cnt[a]
      sep=","
    }
    printf "}"
  }
' "$STATE_FILE")

# ── Schema and Complexity ────────────────────────────────────────────────
SCHEMA=$(grep '^schema:' "$STATE_FILE" | awk '{print $2}')

# ── Output YAML ──────────────────────────────────────────────────────────
cat <<YAML
metrics:
  tokens:
    input: $TOTAL_INPUT
    output: $TOTAL_OUTPUT
    cache_creation: $TOTAL_CACHE_CREATION
    cache_read: $TOTAL_CACHE_READ
    total: $TOTAL_TOKENS
  cost:
    gross_usd: $GROSS_USD
    net_usd: $NET_USD
    model: $MODEL
    pricing:
      input: $INPUT_PRICE
      output: $OUTPUT_PRICE
      cache_read: $CACHE_READ_PRICE
  turns: $TOTAL_TURNS
  tool_calls: $TOTAL_TOOL_CALLS
  api_calls: $TOTAL_TURNS
  wall_clock_minutes: $WALL_CLOCK
  resolution:
    tasks_total: $TASKS_TOTAL
    tasks_planned: $TASKS_TOTAL
    tasks_added: 0
    tasks_completed: $TASKS_COMPLETED
    tasks_failed: $TASKS_FAILED
    resolve_rate: $RESOLVE_RATE
    pass_at_1: $PASS_AT_1
    pass_at_2: $PASS_AT_2
    regressions: 0
    regression_rate: 0.0
  retries:
    total: $RETRY_TOTAL
  human_interventions: 0
  rework_commits: $REWORK_COMMITS
  rework_rate: $REWORK_RATE
  churn:
    files_changed: $FILES_CHANGED
    insertions: $INSERTIONS
    deletions: $DELETIONS
    total_commits: $TOTAL_COMMITS
  review_scores: [$REVIEW_SCORES]
  review_score_avg: $SCORE_AVG
  lint_delta: 0
  category: $SCHEMA
  benchmarks:
    cost_per_task_usd: $COST_PER_TASK
    cost_per_resolution_usd: $COST_PER_RESOLUTION
    tokens_per_task: $TOKENS_PER_TASK
    tokens_per_resolution: $TOKENS_PER_RESOLUTION
    input_output_ratio: $IO_RATIO
    cache_hit_rate: $CACHE_HIT_RATE
  per_agent_tokens: '$PER_AGENT_TOKENS'
YAML
