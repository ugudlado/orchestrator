#!/bin/bash
# compute-swe-metrics.sh — Extract SWE-bench-aligned metrics from state.yaml + git history.
#
# Usage: compute-swe-metrics.sh <state_dir>
#
# Reads:  state.yaml (step_history[].usage for tokens/cost, step_history for resolution)
#         git log (for churn metrics)
# Writes: metrics block to stdout as YAML (for injection into state.yaml)
#
# Token/cost data: primary source is Claude Code session JSONL files (via jq).
# Fallback: state.yaml step_history (only has total_tokens from Agent footer).
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
# Source of truth: $ORCHESTRATOR_HOME/config/pricing.yaml (shared with
# estimate-cost.sh so pre-flight and post-flight use identical rates).
# Fallback when the file is missing keeps CI and old-checkout archives working.
PRICING_FILE="${PRICING_FILE:-${ORCHESTRATOR_HOME:-$(git rev-parse --show-toplevel 2>/dev/null)}/config/pricing.yaml}"

get_pricing() {
  local model="$1"
  if [[ -f "$PRICING_FILE" ]]; then
    # Match the key exactly, fall back to `default:` block.
    awk -v target="$model" '
      /^models:/    { in_models=1; in_default=0; next }
      /^default:/   { in_default=1; in_models=0; next }
      /^[a-z]/ && !/^default:/ && !/^models:/ { in_models=0; in_default=0 }
      (in_models || in_default) && /^  [^ ]/ {
        gsub(/:/, ""); gsub(/^  /, ""); current=$0
      }
      in_models && current == target && /^    input:/      { gsub(/^    input: */, ""); in_v=$0 }
      in_models && current == target && /^    output:/     { gsub(/^    output: */, ""); out_v=$0 }
      in_models && current == target && /^    cache_read:/ { gsub(/^    cache_read: */, ""); cr_v=$0 }
      in_default && /^  input:/      { gsub(/^  input: */, "");      if (in_v=="")  in_v=$0 }
      in_default && /^  output:/     { gsub(/^  output: */, "");     if (out_v=="") out_v=$0 }
      in_default && /^  cache_read:/ { gsub(/^  cache_read: */, ""); if (cr_v=="")  cr_v=$0 }
      END {
        if (in_v=="" || out_v=="" || cr_v=="") { print "15.00 75.00 1.50"; exit }
        print in_v, out_v, cr_v
      }
    ' "$PRICING_FILE"
  else
    # pricing.yaml absent — conservative opus-tier default (same as prior behavior).
    echo "15.00 75.00 1.50"
  fi
}

# ── Session JSONL Parser ─────────────────────────────────────────────────
# Parses Claude Code session JSONL files to extract the full token breakdown.
# Sets TOTAL_INPUT, TOTAL_OUTPUT, TOTAL_CACHE_CREATION, TOTAL_CACHE_READ,
# TOTAL_TURNS, and MODEL from real API usage data.
# Returns 0 on success (caller trusts the updated globals), 1 on any failure.
# Requires: STARTED_AT and COMPLETED_AT to be set before calling.
parse_session_jsonl() {
  local repo_root
  # When running from a git worktree, --show-toplevel returns the worktree path,
  # not the main repo. JSONL is stored under the main repo slug, so use
  # --show-superproject-working-tree (non-empty only inside a worktree) first.
  repo_root=$(git rev-parse --show-superproject-working-tree 2>/dev/null)
  [[ -z "$repo_root" ]] && repo_root=$(git rev-parse --show-toplevel 2>/dev/null)
  [[ -n "$repo_root" ]] || return 1

  # Compute Claude Code project slug: /path/to/repo -> -path-to-repo
  # Claude Code preserves the leading dash from the path's leading slash —
  # don't strip it. Stripping breaks the lookup.
  local slug="${repo_root//\//-}"
  local project_dir="$HOME/.claude/projects/$slug"

  [[ -d "$project_dir" ]] || return 1

  # Convert time window to epoch for entry filtering.
  # Supports macOS (date -j -f) and Linux (date -d) formats.
  # Force UTC interpretation — input strings are ISO 8601 UTC ("...Z"), but
  # macOS `date -j -f` defaults to local TZ, shifting the window and missing
  # all JSONL entries. JSONL timestamps are also UTC; both must be compared in UTC.
  # Normalize timestamps: PyYAML serializes datetimes as "YYYY-MM-DD HH:MM:SS+00:00"
  # instead of ISO 8601 "YYYY-MM-DDTHH:MM:SSZ". Normalize to the T/Z form before parsing.
  local norm_start norm_end
  norm_start=$(echo "$STARTED_AT"   | sed 's/ /T/; s/+00:00$/Z/')
  norm_end=$(echo   "$COMPLETED_AT" | sed 's/ /T/; s/+00:00$/Z/')
  local start_epoch end_epoch
  start_epoch=$(TZ=UTC date -j -f "%Y-%m-%dT%H:%M:%SZ" "$norm_start" "+%s" 2>/dev/null \
    || TZ=UTC date -j -f "%Y-%m-%dT%H:%M:%S" "${norm_start%Z}" "+%s" 2>/dev/null \
    || TZ=UTC date -d "$norm_start" "+%s" 2>/dev/null) || return 1
  end_epoch=$(TZ=UTC date -j -f "%Y-%m-%dT%H:%M:%SZ" "$norm_end" "+%s" 2>/dev/null \
    || TZ=UTC date -j -f "%Y-%m-%dT%H:%M:%S" "${norm_end%Z}" "+%s" 2>/dev/null \
    || TZ=UTC date -d "$norm_end" "+%s" 2>/dev/null) || return 1

  # Enumerate all JSONL files: parent sessions and subagent sessions.
  local jsonl_files
  jsonl_files=$(find "$project_dir" -name "*.jsonl" 2>/dev/null)
  [[ -n "$jsonl_files" ]] || return 1

  # Parse with jq in a single pass:
  # - Filter to final assistant entries: type==assistant, has message.usage, has message.usage.iterations
  # - Filter to entries within the feature time window via .timestamp (epoch)
  # - Sum all token fields across qualifying entries
  # - Select dominant model (most input tokens)
  local result
  # NOTE: .timestamp in JSONL is an ISO 8601 string (e.g. "2026-04-17T03:45:00.123Z").
  # We parse it via fromdateiso8601 then compare against epoch ints.
  # Also: drop the .iterations requirement — current Claude Code JSONL doesn't emit it,
  # and requiring it filters everything out.
  result=$(echo "$jsonl_files" | xargs cat 2>/dev/null | jq -s \
    --argjson start "$start_epoch" --argjson end "$end_epoch" '
    [.[] | select(
      .type == "assistant" and
      .message.usage != null and
      ((.timestamp // "1970-01-01T00:00:00Z") | sub("\\.[0-9]+Z$"; "Z") | fromdateiso8601) >= $start and
      ((.timestamp // "1970-01-01T00:00:00Z") | sub("\\.[0-9]+Z$"; "Z") | fromdateiso8601) <= $end
    )]
    | if length == 0 then error("no entries in time window") else . end
    | {
        input_tokens:  (map(.message.usage.input_tokens // 0) | add // 0),
        output_tokens: (map(.message.usage.output_tokens // 0) | add // 0),
        cache_creation: (map(.message.usage.cache_creation_input_tokens // 0) | add // 0),
        cache_read:    (map(.message.usage.cache_read_input_tokens // 0) | add // 0),
        turns: length,
        model: (group_by(.message.model)
                | map({model: .[0].message.model,
                       total: (map(.message.usage.input_tokens // 0) | add // 0)})
                | sort_by(-.total)
                | .[0].model // "unknown")
      }
  ' 2>/dev/null) || return 1

  # Extract values from jq result JSON object
  local parsed_input parsed_output parsed_cache_creation parsed_cache_read parsed_turns parsed_model
  parsed_input=$(echo "$result" | jq -r '.input_tokens')
  parsed_output=$(echo "$result" | jq -r '.output_tokens')
  parsed_cache_creation=$(echo "$result" | jq -r '.cache_creation')
  parsed_cache_read=$(echo "$result" | jq -r '.cache_read')
  parsed_turns=$(echo "$result" | jq -r '.turns')
  parsed_model=$(echo "$result" | jq -r '.model')

  # Only overwrite globals when we got real data (non-zero input tokens)
  [[ "$parsed_input" =~ ^[0-9]+$ && "$parsed_input" -gt 0 ]] || return 1

  TOTAL_INPUT="$parsed_input"
  TOTAL_OUTPUT="$parsed_output"
  TOTAL_CACHE_CREATION="$parsed_cache_creation"
  TOTAL_CACHE_READ="$parsed_cache_read"
  TOTAL_TURNS="$parsed_turns"
  MODEL="$parsed_model"
  TOTAL_TOKENS=$((TOTAL_INPUT + TOTAL_OUTPUT + TOTAL_CACHE_CREATION))

  return 0
}

# ── Token Counting (from state.yaml step_history) ───────────────────────
# Fallback source: step_history[].usage written by the orchestrator after each agent step.
# The Agent tool footer provides only: total_tokens, tool_uses, duration_ms.
# Granular breakdown (input/output/cache) is always 0 here; JSONL parsing above
# overwrites these if session files are available.
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
  in_sh && in_u && /^      cost_usd:/                     { gsub(/.*: */, ""); cost+=$0+0 }
  in_sh && in_u && /^      tool_uses:/                    { gsub(/.*: */, ""); u+=$0+0 }
  in_sh && in_u && /^      duration_ms:/                  { gsub(/.*: */, ""); d+=$0+0 }
  in_sh && in_u && /^    [a-z]/ && !/^      / { in_u=0 }
  function flush() { if (in_u) in_u=0; s++ }
  END { flush(); printf "%d %d %d %d %d %.6f %d %d %d", inp+0, out+0, cc+0, cr+0, t+0, cost+0, u+0, d+0, s+0 }
' "$STATE_FILE")

TOTAL_INPUT=$(echo "$STATE_USAGE" | awk '{print $1}')
TOTAL_OUTPUT=$(echo "$STATE_USAGE" | awk '{print $2}')
TOTAL_CACHE_CREATION=$(echo "$STATE_USAGE" | awk '{print $3}')
TOTAL_CACHE_READ=$(echo "$STATE_USAGE" | awk '{print $4}')
TOTAL_TOKENS=$(echo "$STATE_USAGE" | awk '{print $5}')
PROXY_COST_USD=$(echo "$STATE_USAGE" | awk '{print $6}')
TOTAL_TOOL_CALLS=$(echo "$STATE_USAGE" | awk '{print $7}')
TOTAL_DURATION_MS=$(echo "$STATE_USAGE" | awk '{print $8}')
TOTAL_STEPS=$(echo "$STATE_USAGE" | awk '{print $9}')

# If total_tokens is 0 but we have input+output, compute it
if [[ "$TOTAL_TOKENS" -eq 0 && "$TOTAL_INPUT" -gt 0 ]]; then
  TOTAL_TOKENS=$((TOTAL_INPUT + TOTAL_OUTPUT + TOTAL_CACHE_CREATION))
fi

# ── Wall Clock Timestamps (needed by parse_session_jsonl) ────────────────
# Extracted early so parse_session_jsonl can use them for time-window filtering.
STARTED_AT=$(grep '^started_at:' "$STATE_FILE" | head -1 | sed "s/^started_at: *//; s/['\"]//g")
COMPLETED_AT=$(grep '^completed_at:' "$STATE_FILE" | head -1 | sed "s/^completed_at: *//; s/['\"]//g")

# ── Session JSONL Token Enrichment ──────────────────────────────────────
# Attempt to read full token breakdown from Claude Code session JONLs.
# Falls back to state.yaml values (zeros for granular fields) if unavailable.
if command -v jq >/dev/null 2>&1 && [[ -n "$STARTED_AT" && -n "$COMPLETED_AT" ]]; then
  parse_session_jsonl || true  # failure is non-blocking
fi

# ── Cost Calculation ─────────────────────────────────────────────────────
# Two cost sources:
#   1. PROXY_COST_USD: summed from step_history[].usage.cost_usd (from LiteLLM pricing lookup)
#   2. Model-based: computed from token counts × hardcoded per-model rates (fallback for native agents)
PRICING=$(get_pricing "$MODEL")
INPUT_PRICE=$(echo "$PRICING" | awk '{print $1}')
OUTPUT_PRICE=$(echo "$PRICING" | awk '{print $2}')
CACHE_READ_PRICE=$(echo "$PRICING" | awk '{print $3}')

# gross_usd: SWE-bench HAL style — ALL tokens at full price, no cache discount.
GROSS_USD=$(echo "scale=4; ($TOTAL_INPUT + $TOTAL_CACHE_CREATION + $TOTAL_CACHE_READ) * $INPUT_PRICE / 1000000 + $TOTAL_OUTPUT * $OUTPUT_PRICE / 1000000" | bc)

# net_usd: Use proxy cost when available (more accurate), fall back to model-based.
MODEL_NET_USD=$(echo "scale=4; ($TOTAL_INPUT + $TOTAL_CACHE_CREATION) * $INPUT_PRICE / 1000000 + $TOTAL_CACHE_READ * $CACHE_READ_PRICE / 1000000 + $TOTAL_OUTPUT * $OUTPUT_PRICE / 1000000" | bc)
HAS_PROXY_COST=$(echo "$PROXY_COST_USD > 0" | bc)
if [[ "$HAS_PROXY_COST" -eq 1 ]]; then
  NET_USD=$PROXY_COST_USD
else
  NET_USD=$MODEL_NET_USD
fi

# ── Wall Clock ───────────────────────────────────────────────────────────
# STARTED_AT and COMPLETED_AT already extracted above (before JSONL enrichment).
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

# ── Schema and Complexity ────────────────────────────────────────────────
# Read schema early so task-resolution section can skip inapplicable work
# for spike/autopilot. The variable is also used later in the output block.
SCHEMA=$(grep '^schema:' "$STATE_FILE" | head -1 | awk '{print $2}')

# ── Task Resolution (from state.yaml) ────────────────────────────────────
# Parse verification_results for task pass/fail/retry counts.
# For spike/autopilot: task resolution is structurally inapplicable —
# these schemas have no task outcomes to resolve. Safe defaults prevent
# division-by-zero; the output block emits explicit null (~) for these fields.
TASKS_FILE="$STATE_DIR/tasks.md"
TASKS_TOTAL=0
TASKS_COMPLETED=0
TASKS_FAILED=0
FIRST_ATTEMPT_PASS=0
RESOLVE_RATE=0
PASS_AT_1=0
PASS_AT_2=0

case "$SCHEMA" in
  spike|autopilot)
    # Skip task resolution calculations — not applicable for these schemas.
    # TASKS_TOTAL=1 prevents division-by-zero in benchmark calculations below.
    TASKS_TOTAL=1
    TASKS_COMPLETED=0
    ;;
  *)
    # feature / bugfix / chore: compute real resolution metrics.
    if [[ -f "$TASKS_FILE" ]]; then
      # Count checked tasks [x] and unchecked [ ]
      TASKS_TOTAL=$(grep -cE '^\s*-\s*\[' "$TASKS_FILE" 2>/dev/null || true)
      TASKS_TOTAL=${TASKS_TOTAL//[$'\n\r ']/}  # strip whitespace/newlines
      TASKS_TOTAL=$((TASKS_TOTAL + 0))
      TASKS_COMPLETED=$(grep -cE '^\s*-\s*\[x\]' "$TASKS_FILE" 2>/dev/null || true)
      TASKS_COMPLETED=${TASKS_COMPLETED//[$'\n\r ']/}
      TASKS_COMPLETED=$((TASKS_COMPLETED + 0))
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
      TASKS_TOTAL=$(grep -c 'step_id: execute-next-task' "$STATE_FILE" 2>/dev/null || true)
      TASKS_TOTAL=${TASKS_TOTAL//[$'\n\r ']/}
      TASKS_TOTAL=$((TASKS_TOTAL + 0))
      TASKS_COMPLETED=$TASKS_TOTAL  # If we got here, they all completed
      FIRST_ATTEMPT_PASS=$TASKS_TOTAL
    fi

    [[ "$TASKS_TOTAL" -eq 0 ]] && TASKS_TOTAL=1  # Avoid division by zero

    RESOLVE_RATE=$(echo "scale=4; $TASKS_COMPLETED / $TASKS_TOTAL" | bc)
    PASS_AT_1=$(echo "scale=4; $FIRST_ATTEMPT_PASS / $TASKS_TOTAL" | bc)

    # pass@2: tasks that succeeded within 2 attempts
    PASS_AT_2_COUNT=$TASKS_COMPLETED  # All completed tasks passed within some number of attempts
    if [[ -n "${TASK_RETRIES:-}" ]]; then
      PASS_AT_2_COUNT=$(echo "$TASK_RETRIES" | awk '$1<=1{c++} END{print c+0}')
    fi
    PASS_AT_2=$(echo "scale=4; $PASS_AT_2_COUNT / $TASKS_TOTAL" | bc)
    ;;
esac

# ── Retries ──────────────────────────────────────────────────────────────
RETRY_TOTAL=$(grep 'retries:' "$STATE_FILE" 2>/dev/null | awk '{s+=$2} END {print s+0}')

REWORK_RATE=0
if [[ "$TOTAL_COMMITS" -gt 0 ]]; then
  REWORK_RATE=$(echo "scale=4; $REWORK_COMMITS / $TOTAL_COMMITS" | bc)
fi

# ── Review Scores ────────────────────────────────────────────────────────
# Extract review scores: look for "overall: N" under review_score blocks.
# For spike/autopilot: review_scores are omitted from output entirely.
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
  in_history && /^[^ -]/ { in_history=0 }
  function flush_entry() {
    if (agent != "" && (total_tokens > 0 || tool_uses > 0 || duration_ms > 0)) {
      tok[agent]  += total_tokens
      cost[agent] += cost_usd
      uses[agent] += tool_uses
      dur[agent]  += duration_ms
      cnt[agent]  += 1
    }
    agent=""; total_tokens=0; cost_usd=0; tool_uses=0; duration_ms=0; in_usage=0
  }
  in_history && /^[[:space:]]*- / { flush_entry() }
  in_history && /^[[:space:]]+agent:/ { gsub(/.*agent: */, ""); gsub(/"/, ""); agent=$0 }
  in_history && /^[[:space:]]+usage:/ { in_usage=1 }
  in_history && in_usage && /^[[:space:]]+total_tokens:/ { gsub(/.*total_tokens: */, ""); total_tokens=$0+0 }
  in_history && in_usage && /^[[:space:]]+cost_usd:/     { gsub(/.*cost_usd: */, "");     cost_usd=$0+0 }
  in_history && in_usage && /^[[:space:]]+tool_uses:/    { gsub(/.*tool_uses: */, "");    tool_uses=$0+0 }
  in_history && in_usage && /^[[:space:]]+duration_ms:/  { gsub(/.*duration_ms: */, "");  duration_ms=$0+0 }
  in_history && in_usage && /^[[:space:]]+[a-z]/ && !/usage:/ { in_usage=0 }
  END {
    flush_entry()
    sep=""
    printf "{"
    for (a in tok) {
      printf "%s\"%s\":{\"total_tokens\":%d,\"cost_usd\":%.6f,\"tool_uses\":%d,\"duration_ms\":%d,\"steps\":%d}",
        sep, a, tok[a], cost[a], uses[a], dur[a], cnt[a]
      sep=","
    }
    printf "}"
  }
' "$STATE_FILE")

# ── Per-Agent Tool Attribution ────────────────────────────────────────────
# Aggregate tools: sub-maps from step_history by agent name.
# Entries without tools: are silently skipped (backward compatible with pre-HL-276).
# Uses "agent\tTool" composite keys (tab-separated) compatible with all awk variants.
PER_AGENT_TOOLS=$(awk '
  /^step_history:/ { in_history=1; next }
  in_history && /^[^ -]/ { in_history=0 }
  in_history && /^[[:space:]]*- / { agent=""; in_usage=0; in_tools=0 }
  in_history && /^[[:space:]]+agent:/ { gsub(/.*agent: */, ""); gsub(/"/, ""); agent=$0 }
  in_history && /^[[:space:]]+usage:/ { in_usage=1 }
  in_history && in_usage && /^[[:space:]]+tools:/        { in_tools=1; next }
  in_history && in_usage && in_tools && /^[[:space:]]+[A-Za-z]/ {
    sub(/^ +/, ""); tool_name=$1; sub(/:$/, "", tool_name)
    key=agent "\t" tool_name
    tool_count[key] += $2
    agent_list[agent]=1
  }
  in_history && in_usage && in_tools && /^[[:space:]]+[a-z]/ && !/[A-Z]/ { in_tools=0 }
  in_history && in_usage && /^[[:space:]]+[a-z]/ && !/usage:/ { in_usage=0; in_tools=0 }
  END {
    agent_sep=""
    printf "{"
    for (a in agent_list) {
      printf "%s\"%s\":{", agent_sep, a
      tool_sep=""
      for (key in tool_count) {
        idx=index(key, "\t")
        if (substr(key, 1, idx-1) == a) {
          printf "%s\"%s\":%d", tool_sep, substr(key, idx+1), tool_count[key]
          tool_sep=","
        }
      }
      printf "}"
      agent_sep=","
    }
    printf "}"
  }
' "$STATE_FILE")

# ── Per-Step Aggregation ─────────────────────────────────────────────────
# Aggregate step_history by step_id: sum total_tokens, tool_uses, duration_ms,
# and count executions (retry-inclusive). Inline steps (agent: inline) appear
# with zero token fields but non-zero duration/tool_uses, consistent with the
# per_agent_tokens bucket for "inline".
# Uses macOS/POSIX-compatible awk (no GNU extensions).
PER_STEP_YAML=$(awk '
  /^step_history:/ { in_history=1; next }
  in_history && /^[^ -]/ { in_history=0 }
  function flush_entry() {
    if (step_id != "") {
      tok[step_id]  += total_tokens
      uses[step_id] += tool_uses
      dur[step_id]  += duration_ms
      cnt[step_id]  += 1
      if (!(step_id in seen_order)) {
        seen_order[step_id] = ++order_seq
      }
    }
    step_id=""; total_tokens=0; tool_uses=0; duration_ms=0; in_usage=0
  }
  in_history && /^[[:space:]]*- step_id:/ {
    flush_entry()
    line=$0; gsub(/^[[:space:]]*- step_id: */, "", line); gsub(/"/, "", line); step_id=line
  }
  in_history && /^[[:space:]]*- / && !/step_id:/ { flush_entry() }
  in_history && /^[[:space:]]+usage:/ { in_usage=1; next }
  in_history && in_usage && /^[[:space:]]+total_tokens:/ { gsub(/.*total_tokens: */, ""); total_tokens=$0+0 }
  in_history && in_usage && /^[[:space:]]+tool_uses:/    { gsub(/.*tool_uses: */, "");    tool_uses=$0+0 }
  in_history && in_usage && /^[[:space:]]+duration_ms:/  { gsub(/.*duration_ms: */, "");  duration_ms=$0+0 }
  in_history && in_usage && /^[[:space:]]+[a-z]/ && !/usage:/ { in_usage=0 }
  END {
    flush_entry()
    # Build per_step YAML — iterate in insertion order by sorting on seen_order value
    n = 0
    for (s in seen_order) {
      n++
      order_arr[n] = seen_order[s]
      step_arr[n] = s
    }
    # Simple insertion sort by order value (macOS/POSIX awk compatible)
    for (i = 2; i <= n; i++) {
      key_o = order_arr[i]; key_s = step_arr[i]
      j = i - 1
      while (j >= 1 && order_arr[j] > key_o) {
        order_arr[j+1] = order_arr[j]; step_arr[j+1] = step_arr[j]; j--
      }
      order_arr[j+1] = key_o; step_arr[j+1] = key_s
    }
    printf "  per_step:\n"
    for (i = 1; i <= n; i++) {
      s = step_arr[i]
      printf "    %s:\n", s
      printf "      total_tokens: %d\n", tok[s]+0
      printf "      tool_uses: %d\n", uses[s]+0
      printf "      duration_ms: %d\n", dur[s]+0
      printf "      executions: %d\n", cnt[s]+0
    }
  }
' "$STATE_FILE")

# ── Estimate vs Actual ────────────────────────────────────────────────────
# Read route_preview.estimate from state.yaml (written by preview-route step
# in the specify/diagnose phase). Compute deltas against actuals so the
# learning loop can tune future estimates. Emits an empty block when no
# estimate exists (cold-start or pre-preview-route feature).
ESTIMATE_TOKENS=$(awk '
  /^route_preview:/ { in_rp=1; next }
  in_rp && /^[a-z]/ { in_rp=0 }
  in_rp && /^  estimate:/ && !/null/ { in_est=1; next }
  in_rp && in_est && /^    tokens:/ { gsub(/^    tokens: */, ""); print; exit }
  in_rp && /^[a-z]/ { in_est=0 }
' "$STATE_FILE")
ESTIMATE_COST=$(awk '
  /^route_preview:/ { in_rp=1; next }
  in_rp && /^[a-z]/ { in_rp=0 }
  in_rp && /^  estimate:/ && !/null/ { in_est=1; next }
  in_rp && in_est && /^    cost_usd:/ { gsub(/^    cost_usd: */, ""); print; exit }
  in_rp && /^[a-z]/ { in_est=0 }
' "$STATE_FILE")

ESTIMATE_BLOCK_YAML=""
if [[ -n "$ESTIMATE_TOKENS" && "$ESTIMATE_TOKENS" != "0" ]]; then
  TOKENS_DELTA_PCT=$(awk -v p="$ESTIMATE_TOKENS" -v a="$TOTAL_TOKENS" \
    'BEGIN { if (p == 0) { print "0.0000"; exit } printf "%.4f", (a - p) / p }')
  COST_DELTA_PCT=$(awk -v p="$ESTIMATE_COST" -v a="$NET_USD" \
    'BEGIN { if (p == 0) { print "0.0000"; exit } printf "%.4f", (a - p) / p }')
  ESTIMATE_BLOCK_YAML="  estimate_vs_actual:
    tokens_predicted: $ESTIMATE_TOKENS
    tokens_actual: $TOTAL_TOKENS
    tokens_delta_pct: $TOKENS_DELTA_PCT
    cost_predicted_usd: $ESTIMATE_COST
    cost_actual_usd: $NET_USD
    cost_delta_pct: $COST_DELTA_PCT
"
fi

# ── Regression Count ──────────────────────────────────────────────────────
# A regression is a step_history entry with a `regression:` block (written by
# execute-next-task step 5a when a task drops the full-suite pass count).
# If no baseline was captured or no regressions were recorded, this is 0.
REGRESSIONS=$(awk '
  /^step_history:/ { in_history=1; next }
  in_history && /^[^ ]/ && !/^  / { in_history=0 }
  in_history && /^    regression:/ { count++ }
  END { print count+0 }
' "$STATE_FILE")

if [ "$TASKS_TOTAL" -gt 0 ]; then
  REGRESSION_RATE=$(awk -v r="$REGRESSIONS" -v t="$TASKS_TOTAL" 'BEGIN { printf "%.4f", r/t }')
else
  REGRESSION_RATE="0.0"
fi

# ── Schema-dispatched output blocks ──────────────────────────────────────
# Resolution block and review_scores differ by schema:
#   spike/autopilot  → resolution fields are explicit null (~), no review_scores
#   feature/bugfix/chore → full resolution block with real values, review_scores included
# Pre-compute both blocks as strings so the final output is a single heredoc.
case "$SCHEMA" in
  spike|autopilot)
    RESOLUTION_BLOCK="  resolution:
    resolve_rate: ~
    pass_at_1: ~
    pass_at_2: ~
    regression_rate: ~
    tasks_total: ~"
    REVIEW_BLOCK=""
    ;;
  *)
    RESOLUTION_BLOCK="  resolution:
    tasks_total: $TASKS_TOTAL
    tasks_planned: $TASKS_TOTAL
    tasks_added: 0
    tasks_completed: $TASKS_COMPLETED
    tasks_failed: $TASKS_FAILED
    resolve_rate: $RESOLVE_RATE
    pass_at_1: $PASS_AT_1
    pass_at_2: $PASS_AT_2
    regressions: $REGRESSIONS
    regression_rate: $REGRESSION_RATE"
    # Trailing newline baked in so the heredoc interpolates a clean block or nothing.
    REVIEW_BLOCK="  review_scores: [$REVIEW_SCORES]
  review_score_avg: $SCORE_AVG
"
    ;;
esac

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
$RESOLUTION_BLOCK
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
$REVIEW_BLOCK  lint_delta: 0
  category: $SCHEMA
  benchmarks:
    cost_per_task_usd: $COST_PER_TASK
    cost_per_resolution_usd: $COST_PER_RESOLUTION
    tokens_per_task: $TOKENS_PER_TASK
    tokens_per_resolution: $TOKENS_PER_RESOLUTION
    input_output_ratio: $IO_RATIO
    cache_hit_rate: $CACHE_HIT_RATE
${ESTIMATE_BLOCK_YAML}  per_agent_tokens: '$PER_AGENT_TOKENS'
  per_agent_tools: '$PER_AGENT_TOOLS'
$PER_STEP_YAML
YAML
