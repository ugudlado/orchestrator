#!/usr/bin/env bash
# compute-swe-metrics.sh — Queries feature_report view via duckdb -readonly -json.
#
# Usage: compute-swe-metrics.sh <state_dir>
#
# Reads:  <state_dir>/state.yaml (for change_id only)
# Writes: metrics block to stdout as YAML (for injection into state.yaml)
#
# Data source: DuckDB feature_report view — no Python intermediary, no CLI shell-out.
# Rewritten as part of Phase 3 (report-views-retire-cli).

set -euo pipefail

STATE_DIR="${1:?Usage: compute-swe-metrics.sh <state_dir>}"
STATE_YAML="$STATE_DIR/state.yaml"

if [[ ! -f "$STATE_YAML" ]]; then
  echo "ERROR: state.yaml not found at $STATE_YAML" >&2
  exit 1
fi

# Resolve ORCHESTRATOR_HOME: env var → git rev-parse fallback
ORCHESTRATOR_HOME="${ORCHESTRATOR_HOME:-$(git rev-parse --show-toplevel 2>/dev/null || echo "")}"
if [[ -z "$ORCHESTRATOR_HOME" ]]; then
  echo "ERROR: ORCHESTRATOR_HOME is not set and git rev-parse failed" >&2
  exit 1
fi

# Resolve DB path: METRICS_DB env var → ORCHESTRATOR_HOME/metrics.duckdb
DB_PATH="${METRICS_DB:-$ORCHESTRATOR_HOME/metrics.duckdb}"

CHANGE_ID=$(yq -r '.change_id' "$STATE_YAML")
if [[ -z "$CHANGE_ID" || "$CHANGE_ID" == "null" ]]; then
  echo "ERROR: change_id not found in $STATE_YAML" >&2
  exit 1
fi

# Slug-guard: change_id must match ^[a-z0-9][a-z0-9-]*$ before embedding in SQL
if ! echo "$CHANGE_ID" | grep -qE '^[a-z0-9][a-z0-9-]*$'; then
  echo "ERROR: change_id '$CHANGE_ID' violates slug guard" >&2
  exit 3
fi

# Query feature_report view — returns one row per (repo_root, change_id)
set +e
JSON=$(duckdb -readonly -json "$DB_PATH" \
  -c "SELECT * FROM feature_report WHERE change_id = '$CHANGE_ID'" \
  2>"${TMPDIR:-/tmp}/csm-err-$$.txt")
DUCK_EXIT=$?
set -e

if [[ "$DUCK_EXIT" -ne 0 ]]; then
  echo "ERROR: duckdb query failed for change_id=$CHANGE_ID" >&2
  cat "${TMPDIR:-/tmp}/csm-err-$$.txt" >&2
  rm -f "${TMPDIR:-/tmp}/csm-err-$$.txt"
  exit 1
fi
rm -f "${TMPDIR:-/tmp}/csm-err-$$.txt"

# Timestamp for provenance — allow override for test determinism
TS="${COMPUTE_SWE_SOURCE_TS:-$(date -u +"%Y-%m-%dT%H:%M:%SZ")}"

echo "$JSON" | python3 -c "
import sys, json, yaml
rows = json.load(sys.stdin)
if not rows:
    sys.stderr.write('ERROR: no events for change_id\n'); sys.exit(1)
r = rows[0]
# DuckDB -json returns BIGINT columns as strings; cast to int explicitly
def rint(v): return int(v) if v is not None else 0
# Re-parse and re-dump JSON string columns with sort_keys for determinism (D-6)
per_agent_tokens = json.dumps(json.loads(r['per_agent_tokens']), sort_keys=True)
per_agent_tools  = json.dumps(json.loads(r['per_agent_tools']),  sort_keys=True)
per_tool_uses    = json.dumps(json.loads(r['per_tool_uses']),    sort_keys=True)
per_step_dict    = json.loads(r['per_step'])
# Review scores: parse review_scores_json (string) -> list
review_scores = []
if r.get('review_scores_json'):
    try: review_scores = json.loads(r['review_scores_json'])
    except Exception: review_scores = []
turns_val = rint(r['turns'])
metrics = {
    'tokens': {
        'input':          rint(r['input_tokens']),
        'output':         rint(r['output_tokens']),
        'cache_creation': rint(r['cache_creation_input_tokens']),
        'cache_read':     rint(r['cache_read_input_tokens']),
        'total':          rint(r['total_tokens']),
    },
    'cost': {
        'net_usd':   r['cost_usd'],
        'gross_usd': r['gross_usd'],
        'model':     r['model'],
        'pricing': {
            'input':          r['pricing_input_usd'],
            'output':         r['pricing_output_usd'],
            'cache_read':     r['pricing_cache_read_usd'],
            'cache_creation': r['pricing_cache_creation_usd'],
        },
    },
    'turns':              turns_val,
    'api_calls':          turns_val,
    'tool_calls':         r['tool_calls_count'],
    'wall_clock_minutes': r['wall_clock_minutes'],
    'category':           r['category'],
    'human_interventions': r['human_interventions'],
    'rework_commits':     r['rework_commits'],
    'rework_rate':        r['rework_rate'],
    'resolution': {k: r[k] for k in ('tasks_total','tasks_planned','tasks_added',
        'tasks_completed','tasks_failed','resolve_rate','pass_at_1','pass_at_2',
        'regressions','regression_rate')},
    'retries': {'total': r['retries_total']},
    'churn':   {k: r[k] for k in ('files_changed','insertions','deletions','total_commits')},
    'review_scores':     review_scores,
    'review_score_avg':  r['review_score_avg'],
    'lint_delta':        0,
    'benchmarks': {k: r[k] for k in ('cost_per_task_usd','cost_per_resolution_usd',
        'tokens_per_task','tokens_per_resolution','input_output_ratio','cache_hit_rate')},
    'per_agent_tokens':  per_agent_tokens,
    'per_agent_tools':   per_agent_tools,
    'per_tool_uses':     per_tool_uses,
    'per_step':          per_step_dict,
    'source':            'duckdb@$TS',
}
print(yaml.safe_dump({'metrics': metrics}, sort_keys=True, default_flow_style=False), end='')
"
