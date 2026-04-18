#!/bin/bash
# estimate-cost.sh — Pre-flight cost/token estimate from archive history.
#
# Usage: estimate-cost.sh <state_dir>
#
# Reads:
#   - <state_dir>/state.yaml           (current schema, tasks_planned hint)
#   - <state_dir>/tasks.md             (tasks_planned count)
#   - $REPO_ROOT/scripts/routes.yaml   (agent → backend → model)
#   - $ORCHESTRATOR_HOME/config/pricing.yaml  (model → $/1M tokens)
#   - $REPO_ROOT/spec/changes/archive/*/state.yaml  (history, same-schema only)
#
# Writes:
#   - stdout: YAML `route_preview:` block (for state.yaml injection)
#   - stderr: rendered preview table
#
# Estimator is descriptive, not predictive: median(tokens_per_task) × tasks_planned,
# split by per_agent_tokens share from archive, priced via pricing.yaml. No caps,
# no gates. Cold-start emits `estimate: null` with reason.

set -uo pipefail

ARG="${1:?Usage: estimate-cost.sh <state_dir|state.yaml>}"
if [[ -f "$ARG" ]]; then
  STATE_FILE="$ARG"
  STATE_DIR="$(dirname "$ARG")"
else
  STATE_DIR="$ARG"
  STATE_FILE="$STATE_DIR/state.yaml"
fi
TASKS_FILE="$STATE_DIR/tasks.md"

if [[ ! -f "$STATE_FILE" ]]; then
  echo "ERROR: state.yaml not found at $STATE_FILE" >&2
  exit 1
fi

REPO_ROOT="${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null)}"
ORCHESTRATOR_HOME="${ORCHESTRATOR_HOME:-$REPO_ROOT}"
ROUTES_FILE="${ROUTES_FILE:-$REPO_ROOT/scripts/routes.yaml}"
PRICING_FILE="${PRICING_FILE:-$ORCHESTRATOR_HOME/config/pricing.yaml}"
ARCHIVE_GLOB="${ARCHIVE_GLOB:-$REPO_ROOT/spec/changes/archive/*/state.yaml}"

SCHEMA=$(awk '/^schema:/ {print $2; exit}' "$STATE_FILE" | tr -d '"')
[[ -z "$SCHEMA" ]] && SCHEMA="feature"

# ── Tasks planned ────────────────────────────────────────────────────────
# Count checkbox lines in tasks.md. Fall back to 1 to keep the estimator
# scalar — "unknown fan-out" is not the estimator's job, it's a prior.
TASKS_PLANNED=0
if [[ -f "$TASKS_FILE" ]]; then
  TASKS_PLANNED=$(grep -cE '^\s*-\s*\[' "$TASKS_FILE" 2>/dev/null || echo 0)
  TASKS_PLANNED=${TASKS_PLANNED//[$'\n\r ']/}
fi
[[ "$TASKS_PLANNED" -eq 0 ]] && TASKS_PLANNED=1

# ── Parse routes.yaml: agent → model ─────────────────────────────────────
# Two-pass awk. First walk collects agent→backend under `agents:`.
# Second walk resolves each backend under `models:` to its `model:` string.
# Native agents (backend starts with `native_`) resolve to a claude-* model
# string based on the suffix (opus/sonnet/haiku) mapped to the current
# preferred Claude Code release, because native_* has no model: field.
declare -A AGENT_BACKEND
declare -A BACKEND_MODEL

if [[ -f "$ROUTES_FILE" ]]; then
  # Pass 1: agents block
  while IFS=':' read -r agent backend; do
    agent=$(echo "$agent" | tr -d ' ')
    backend=$(echo "$backend" | tr -d ' ')
    [[ -z "$agent" || -z "$backend" ]] && continue
    AGENT_BACKEND[$agent]="$backend"
  done < <(awk '
    /^agents:/ { in_block=1; next }
    /^[a-z]/   { in_block=0 }
    in_block && /^  [a-z_-]+:/ { gsub(/^  /, ""); print }
  ' "$ROUTES_FILE")

  # Pass 2: models block — capture each backend's `model:` line
  while IFS='|' read -r backend model; do
    [[ -z "$backend" || -z "$model" ]] && continue
    BACKEND_MODEL[$backend]="$model"
  done < <(awk '
    /^models:/ { in_block=1; next }
    /^[a-z]/   { in_block=0 }
    in_block && /^  [a-z_-]+:/ {
      gsub(/:/, ""); gsub(/^  /, "")
      current=$0
      next
    }
    in_block && /^    model:/ {
      gsub(/^    model: */, "")
      gsub(/^"|"$/, "")
      print current "|" $0
    }
  ' "$ROUTES_FILE")
fi

# Resolve `native_<tier>` → current Claude Code release (opus-4-7 etc.)
# Source of truth: model-id strings seen in Claude Code session JSONL.
resolve_native() {
  local backend="$1"
  case "$backend" in
    native_opus)   echo "claude-opus-4-7" ;;
    native_sonnet) echo "claude-sonnet-4-6" ;;
    native_haiku)  echo "claude-haiku-4-5" ;;
    *)             echo "$backend" ;;
  esac
}

# ── Parse pricing.yaml: model → {input, output, cache_read} ──────────────
# Keyed by model string. `default:` is the fallback when a model isn't listed.
lookup_pricing() {
  local model="$1"
  awk -v target="$model" '
    /^models:/ { in_models=1; in_default=0; next }
    /^default:/ { in_default=1; in_models=0; next }
    /^[a-z]/ && !/^default:/ && !/^models:/ { in_models=0; in_default=0 }
    (in_models || in_default) && /^  [^ ]/ {
      gsub(/:/, "")
      gsub(/^  /, "")
      current = $0
    }
    in_models && current == target && /^    input:/        { gsub(/^    input: */, ""); in_v=$0 }
    in_models && current == target && /^    output:/       { gsub(/^    output: */, ""); out_v=$0 }
    in_models && current == target && /^    cache_read:/   { gsub(/^    cache_read: */, ""); cr_v=$0 }
    in_default && /^  input:/      { gsub(/^  input: */, ""); if (in_v == "") in_v=$0 }
    in_default && /^  output:/     { gsub(/^  output: */, ""); if (out_v == "") out_v=$0 }
    in_default && /^  cache_read:/ { gsub(/^  cache_read: */, ""); if (cr_v == "") cr_v=$0 }
    END {
      if (in_v == "" || out_v == "" || cr_v == "") { print "15.00 75.00 1.50"; exit }
      print in_v, out_v, cr_v
    }
  ' "$PRICING_FILE"
}

# ── Scan archive for same-schema history ─────────────────────────────────
# Collect per-feature: tokens_per_task, tasks_total, per_agent_tokens JSON.
# We compute a median across features, not a weighted aggregate — the
# unit of observation is "one feature".
TOKENS_PER_TASK_LIST=""
PER_AGENT_JSON_LIST=""
ARCHIVE_COUNT=0

for archive_state in $ARCHIVE_GLOB; do
  [[ -f "$archive_state" ]] || continue
  arc_schema=$(awk '/^schema:/ {print $2; exit}' "$archive_state" | tr -d '"')
  [[ "$arc_schema" != "$SCHEMA" ]] && continue

  tpt=$(awk '
    /^  benchmarks:/ { in_b=1; next }
    in_b && /^    tokens_per_task:/ { gsub(/^    tokens_per_task: */, ""); print; exit }
    in_b && /^  [a-z]/ { in_b=0 }
  ' "$archive_state")

  pat=$(awk '
    /^  per_agent_tokens:/ {
      gsub(/^  per_agent_tokens: *'"'"'/, "")
      gsub(/'"'"'$/, "")
      print; exit
    }
  ' "$archive_state")

  if [[ -n "$tpt" && "$tpt" != "0" ]]; then
    TOKENS_PER_TASK_LIST="$TOKENS_PER_TASK_LIST $tpt"
    PER_AGENT_JSON_LIST="$PER_AGENT_JSON_LIST;$pat"
    ARCHIVE_COUNT=$((ARCHIVE_COUNT + 1))
  fi
done

# ── Median tokens_per_task ───────────────────────────────────────────────
median() {
  local list="$1"
  [[ -z "$list" ]] && { echo 0; return; }
  echo "$list" | tr ' ' '\n' | grep -v '^$' | sort -n | awk '
    { a[NR]=$1 }
    END {
      if (NR == 0) { print 0; exit }
      if (NR % 2) { print a[(NR+1)/2] } else { print (a[NR/2] + a[NR/2+1]) / 2 }
    }
  '
}

TOKENS_PER_TASK_MEDIAN=$(median "$TOKENS_PER_TASK_LIST")

# ── Per-agent share (mean across history) ────────────────────────────────
# Each archive entry has per_agent_tokens like:
#   {"developer":{"total_tokens":5000,...},"reviewer":{"total_tokens":2000,...}}
# We compute mean(agent_tokens / feature_total) across features that
# observed that agent. Agents not seen in any archive get share 0 and are
# still rendered in the preview (no cost estimate).
# BSD awk (macOS default) does not support gawk 3-arg match(s, re, arr).
# Use a portable two-phase approach: tr to put each agent on its own line,
# then a plain match() + substr() pair.
PER_AGENT_SHARE=$(printf '%s\n' "$PER_AGENT_JSON_LIST" | tr ';' '\n' | awk '
  {
    json = $0
    if (json == "" || json == "{}") next
    delete agent_tok
    total = 0
    while (1) {
      if (match(json, /"[a-z_-]+":\{[^}]*"total_tokens":[0-9]+/) == 0) break
      seg = substr(json, RSTART, RLENGTH)
      json = substr(json, RSTART + RLENGTH)
      # Extract agent name
      if (match(seg, /"[a-z_-]+"/) == 0) continue
      agent = substr(seg, RSTART+1, RLENGTH-2)
      # Extract total_tokens
      if (match(seg, /"total_tokens":[0-9]+/) == 0) continue
      tok = substr(seg, RSTART+15, RLENGTH-15) + 0
      agent_tok[agent] = tok
      total += tok
    }
    if (total == 0) next
    for (a in agent_tok) {
      share_sum[a] += agent_tok[a] / total
      share_cnt[a] += 1
    }
  }
  END {
    for (a in share_sum) {
      printf "%s %.6f\n", a, share_sum[a] / share_cnt[a]
    }
  }
')

# ── Build route_preview YAML + human table ───────────────────────────────
# Collect agents: union of (routes.yaml agents) ∪ (observed in archive share).
declare -A ALL_AGENTS
for a in "${!AGENT_BACKEND[@]}"; do ALL_AGENTS[$a]=1; done
while IFS=' ' read -r agent share; do
  [[ -n "$agent" ]] && ALL_AGENTS[$agent]=1
done <<< "$PER_AGENT_SHARE"

get_share() {
  echo "$PER_AGENT_SHARE" | awk -v a="$1" '$1 == a { print $2; exit }'
}

# Compose both outputs in a single pass so the per-agent lines stay aligned.
AGENTS_YAML=""
AGENTS_TABLE=""
TOTAL_TOKENS_EST=0
TOTAL_COST_EST=0

for agent in $(echo "${!ALL_AGENTS[@]}" | tr ' ' '\n' | sort); do
  backend="${AGENT_BACKEND[$agent]:-unrouted}"
  if [[ "$backend" == native_* ]]; then
    model=$(resolve_native "$backend")
  else
    model="${BACKEND_MODEL[$backend]:-unknown}"
  fi

  read -r in_price out_price cache_price < <(lookup_pricing "$model")
  share=$(get_share "$agent")
  [[ -z "$share" ]] && share=0

  # Total feature tokens from history × share = this agent's projected tokens.
  agent_tokens=0
  agent_cost=0
  if [[ "$ARCHIVE_COUNT" -gt 0 && "$TOKENS_PER_TASK_MEDIAN" != "0" ]]; then
    feature_tokens=$(awk -v tpt="$TOKENS_PER_TASK_MEDIAN" -v n="$TASKS_PLANNED" 'BEGIN { printf "%.0f", tpt * n }')
    agent_tokens=$(awk -v ft="$feature_tokens" -v s="$share" 'BEGIN { printf "%.0f", ft * s }')
    # Treat all projected tokens as input-priced (cache mix unknown at pre-flight).
    # Output is typically ~10% of input in the archive; use a simple 10/90 split.
    agent_cost=$(awk -v t="$agent_tokens" -v ip="$in_price" -v op="$out_price" \
      'BEGIN { printf "%.4f", (t * 0.9 * ip / 1000000) + (t * 0.1 * op / 1000000) }')
  fi

  TOTAL_TOKENS_EST=$(awk -v a="$TOTAL_TOKENS_EST" -v b="$agent_tokens" 'BEGIN { printf "%.0f", a + b }')
  TOTAL_COST_EST=$(awk -v a="$TOTAL_COST_EST" -v b="$agent_cost" 'BEGIN { printf "%.4f", a + b }')

  AGENTS_YAML="${AGENTS_YAML}    - agent: $agent
      backend: $backend
      model: $model
      pricing: { input: $in_price, output: $out_price, cache_read: $cache_price }
      share: $share
      tokens_estimate: $agent_tokens
      cost_estimate_usd: $agent_cost
"
  AGENTS_TABLE="${AGENTS_TABLE}$(printf "  %-18s %-12s %-36s %8s %8s %8s\n" "$agent" "$backend" "$model" "$in_price" "$out_price" "$cache_price")
"
done

# ── Emit YAML (stdout) ───────────────────────────────────────────────────
if [[ "$ARCHIVE_COUNT" -eq 0 ]]; then
  ESTIMATE_BLOCK="  estimate: null
  estimate_reason: no_history"
else
  ESTIMATE_BLOCK="  estimate:
    tokens: $TOTAL_TOKENS_EST
    cost_usd: $TOTAL_COST_EST
    tasks_planned: $TASKS_PLANNED
    tokens_per_task_median: $TOKENS_PER_TASK_MEDIAN
    archive_sample_size: $ARCHIVE_COUNT"
fi

cat <<YAML
route_preview:
  schema: $SCHEMA
  generated_at: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
  agents:
${AGENTS_YAML}${ESTIMATE_BLOCK}
YAML

# ── Emit human table (stderr) ────────────────────────────────────────────
{
  echo ""
  echo "═══ ROUTE PREVIEW — schema: $SCHEMA ═══"
  printf "  %-18s %-12s %-36s %8s %8s %8s\n" "AGENT" "BACKEND" "MODEL" "\$/1M in" "\$/1M out" "\$/1M cr"
  printf "  %-18s %-12s %-36s %8s %8s %8s\n" "------------------" "------------" "------------------------------------" "--------" "--------" "--------"
  printf "%s" "$AGENTS_TABLE"
  echo ""
  if [[ "$ARCHIVE_COUNT" -eq 0 ]]; then
    echo "  ESTIMATE: no archive history — cold start. Preview shows routing only."
  else
    echo "  ESTIMATE (median of $ARCHIVE_COUNT archived ${SCHEMA}s)"
    printf "    Tasks planned:   %d\n" "$TASKS_PLANNED"
    printf "    Tokens/task:     %s (median)\n" "$TOKENS_PER_TASK_MEDIAN"
    printf "    Total tokens:    %s\n" "$TOTAL_TOKENS_EST"
    printf "    Total cost:      \$%s USD\n" "$TOTAL_COST_EST"
  fi
  echo ""
  echo "  Informational only. No budget cap. Actuals recorded at complete phase."
  echo ""
} >&2
