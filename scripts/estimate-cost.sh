#!/bin/bash
# estimate-cost.sh — Pre-flight cost/token estimate from archive history.
#
# Usage: estimate-cost.sh <state_dir|state.yaml>
#
# Reads:
#   - <state_dir>/state.yaml           (current schema, tasks_planned hint)
#   - <state_dir>/tasks.yaml             (tasks_planned count)
#   - $REPO_ROOT/scripts/routes.yaml   (agent names for the routes side of the union)
#   - $METRICS_DB, else $ORCHESTRATOR_HOME/metrics.duckdb (DuckDB file — model → $/1M tokens)
#     Path convention matches record.py's `main()` — see config/scripts/orchestrator_next/record.py.
#   - $REPO_ROOT/spec/changes/archive/*/state.yaml  (history, same-schema only)
#
# Writes:
#   - stdout: YAML `route_preview:` block (for state.yaml injection)
#   - stderr: rendered preview table
#
# Estimator is descriptive, not predictive: median(tokens_per_task) × tasks_planned,
# split by per_agent_tokens share from archive, priced via the DuckDB pricing table.
# No caps, no gates. Cold-start emits `estimate: null` with reason.
#
# Pricing logic is delegated (ORC-71) to the shared Python module:
#   python3 -m orchestrator_next.pricing --agents <agents…>
# This script owns agent-list assembly (routes ∪ archive-observed union); the
# pricing CLI is a pure pricer. When the metrics DB is absent the CLI fails loud
# (Decision D-2) and this script propagates the non-zero exit — no fabricated
# fallback rates.
#
# Bash 3.2 compatible — no declare -A, no mapfile, no readarray, no ${var^^}.

set -uo pipefail

ARG="${1:?Usage: estimate-cost.sh <state_dir|state.yaml>}"
if [ -f "$ARG" ]; then
  STATE_FILE="$ARG"
  STATE_DIR="$(dirname "$ARG")"
else
  STATE_DIR="$ARG"
  STATE_FILE="$STATE_DIR/state.yaml"
fi
TASKS_FILE="$STATE_DIR/tasks.yaml"

if [ ! -f "$STATE_FILE" ]; then
  echo "ERROR: state.yaml not found at $STATE_FILE" >&2
  exit 1
fi

REPO_ROOT="${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null)}"
ORCHESTRATOR_HOME="${ORCHESTRATOR_HOME:-$REPO_ROOT}"
# ORC-105: routes merged into config/agents.yaml; legacy scripts/routes.yaml fallback.
ROUTES_FILE="${ROUTES_FILE:-$REPO_ROOT/config/agents.yaml}"
if [ ! -f "$ROUTES_FILE" ]; then ROUTES_FILE="$REPO_ROOT/scripts/routes.yaml"; fi
ARCHIVE_GLOB="${ARCHIVE_GLOB:-$REPO_ROOT/spec/changes/archive/*/state.yaml}"

SCHEMA=$(awk '/^schema:/ {print $2; exit}' "$STATE_FILE" | tr -d '"')
[ -z "$SCHEMA" ] && SCHEMA="feature"

# ── Tasks planned ────────────────────────────────────────────────────────
# Count entries in tasks.yaml. Fall back to 1 to keep the estimator
# scalar — "unknown fan-out" is not the estimator's job, it's a prior.
TASKS_PLANNED=0
if [ -f "$TASKS_FILE" ]; then
  TASKS_PLANNED=$(grep -cE '^\s*-\s*\[' "$TASKS_FILE" 2>/dev/null || echo 0)
  TASKS_PLANNED=${TASKS_PLANNED//[$'\n\r ']/}
fi
[ "$TASKS_PLANNED" -eq 0 ] && TASKS_PLANNED=1

# ── Routes agents (routes side of the agent-list union) ──────────────────
# A small awk pass over routes.yaml's `agents:` block, emitting one agent name
# per line. This is the routes side of the routes ∪ archive-observed union; the
# pricing resolution itself is delegated to the Python CLI (ORC-71).
ROUTES_AGENTS=""
if [ -f "$ROUTES_FILE" ]; then
  ROUTES_AGENTS=$(awk '
    /^agents:/ { in_block=1; next }
    /^[a-z]/   { in_block=0 }
    in_block && /^  [a-z_-]+:/ {
      line = $0
      gsub(/^  /, "", line)
      n = split(line, parts, /:[[:space:]]*/)
      if (n >= 1) {
        agent = parts[1]
        gsub(/[[:space:]]/, "", agent)
        print agent
      }
    }
  ' "$ROUTES_FILE")
fi

# ── Scan archive for same-schema history ─────────────────────────────────
# Collect per-feature: tokens_per_task, tasks_total, per_agent_tokens JSON.
# We compute a median across features, not a weighted aggregate — the
# unit of observation is "one feature".
TOKENS_PER_TASK_LIST=""
PER_AGENT_JSON_LIST=""
ARCHIVE_COUNT=0

for archive_state in $ARCHIVE_GLOB; do
  [ -f "$archive_state" ] || continue
  arc_schema=$(awk '/^schema:/ {print $2; exit}' "$archive_state" | tr -d '"')
  [ "$arc_schema" != "$SCHEMA" ] && continue

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

  if [ -n "$tpt" ] && [ "$tpt" != "0" ]; then
    TOKENS_PER_TASK_LIST="$TOKENS_PER_TASK_LIST $tpt"
    PER_AGENT_JSON_LIST="$PER_AGENT_JSON_LIST;$pat"
    ARCHIVE_COUNT=$((ARCHIVE_COUNT + 1))
  fi
done

# ── Median tokens_per_task ───────────────────────────────────────────────
median() {
  local list="$1"
  [ -z "$list" ] && { echo 0; return; }
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

# ── Build the set of all agents ───────────────────────────────────────────
# Union of (routes.yaml agents) ∪ (observed in archive share).
# Stored as a newline-delimited list of unique agent names — bash 3.2 compatible
# (no declare -A).

ALL_AGENTS_LIST="$ROUTES_AGENTS"

# Add agents from PER_AGENT_SHARE (may include agents not in routes.yaml)
if [ -n "$PER_AGENT_SHARE" ]; then
  ALL_AGENTS_LIST=$(printf '%s\n%s\n' "$ALL_AGENTS_LIST" \
    "$(echo "$PER_AGENT_SHARE" | awk '{print $1}')")
fi

# Deduplicate and sort
ALL_AGENTS_LIST=$(echo "$ALL_AGENTS_LIST" | grep -v '^$' | sort -u)

get_share() {
  echo "$PER_AGENT_SHARE" | awk -v a="$1" '$1 == a { print $2; exit }'
}

# ── Price all agents in one CLI call (ORC-71) ────────────────────────────
# Delegate routes resolution + DuckDB pricing to the shared Python module.
# One subprocess spawn per preview (Decision D-6). The CLI is a pure pricer:
# it prices exactly the agents we pass. If the metrics DB is absent the CLI
# fails loud (Decision D-2) and we propagate the non-zero exit — no fabricated
# fallback rates.
#
# PRICING_ROWS is newline-delimited "agent|backend|model|input|output|cache_read".
PRICING_ROWS=""
if [ -n "$ALL_AGENTS_LIST" ]; then
  AGENTS_ARGS=$(echo "$ALL_AGENTS_LIST" | tr '\n' ' ')
  # shellcheck disable=SC2086  # word-splitting $AGENTS_ARGS into separate --agents values is intended
  # ORC-106: orchestrator_next package lives in the repo (REPO_ROOT), not under
  # the installed ORCHESTRATOR_HOME symlink dir (which only links config/ + scripts/).
  PRICING_JSON=$(PYTHONPATH="$REPO_ROOT" \
    python3 -m orchestrator_next.pricing --agents $AGENTS_ARGS)
  CLI_RC=$?
  if [ "$CLI_RC" -ne 0 ]; then
    echo "ERROR: pricing CLI failed (exit $CLI_RC); cannot produce cost estimate" >&2
    exit "$CLI_RC"
  fi
  PRICING_ROWS=$(echo "$PRICING_JSON" | python3 -c '
import json, sys
for o in json.load(sys.stdin):
    b = o["backend"] if o["backend"] is not None else "unrouted"
    m = o["model"] if o["model"] is not None else "unknown"
    print("%s|%s|%s|%s|%s|%s" % (
        o["agent"], b, m,
        o["input_usd"], o["output_usd"], o["cache_read_usd"]))
')
fi

# ── Compose YAML + human table ────────────────────────────────────────────
AGENTS_YAML=""
AGENTS_TABLE=""
TOTAL_TOKENS_EST=0
TOTAL_COST_EST=0

while IFS= read -r agent; do
  [ -z "$agent" ] && continue

  # Look up this agent's priced row from the CLI output.
  row=$(echo "$PRICING_ROWS" | awk -F'|' -v a="$agent" '$1 == a { print; exit }')
  IFS='|' read -r _ backend model in_price out_price cache_price <<EOF
$row
EOF
  [ -z "$backend" ]    && backend="unrouted"
  [ -z "$model" ]      && model="unknown"
  [ -z "$in_price" ]   && in_price=0
  [ -z "$out_price" ]  && out_price=0
  [ -z "$cache_price" ] && cache_price=0

  share=$(get_share "$agent")
  [ -z "$share" ] && share=0

  # Total feature tokens from history × share = this agent's projected tokens.
  agent_tokens=0
  agent_cost=0
  if [ "$ARCHIVE_COUNT" -gt 0 ] && [ "$TOKENS_PER_TASK_MEDIAN" != "0" ]; then
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
done <<EOF
$ALL_AGENTS_LIST
EOF

# ── Emit YAML (stdout) ───────────────────────────────────────────────────
if [ "$ARCHIVE_COUNT" -eq 0 ]; then
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
  generated_at: "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
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
  if [ "$ARCHIVE_COUNT" -eq 0 ]; then
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
