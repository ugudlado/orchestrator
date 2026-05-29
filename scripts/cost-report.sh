#!/usr/bin/env bash
# cost-report.sh — Markdown cost summary for workflow-complete.
# Usage: cost-report.sh --change-id <cid> [--tail]
# Reads: $METRICS_DB or $ORCHESTRATOR_HOME/metrics.duckdb
# Writes: full 8-section markdown report to stdout (default)
#         --tail: single summary line: "<id>: $X.XX · Ym · Z steps · Nx median"
# Exit codes:
#   0 — success
#   1 — no events for change_id (or DB/query error)
#   3 — slug-guard violation or missing required argument

set -uo pipefail

# ── Argument parsing ──────────────────────────────────────────────────────────
CHANGE_ID=""
TAIL_MODE=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --change-id)
      CHANGE_ID="$2"
      shift 2
      ;;
    --tail)
      TAIL_MODE=true
      shift
      ;;
    *)
      echo "error: unknown argument '$1'" >&2
      echo "Usage: cost-report.sh --change-id <change-id> [--tail]" >&2
      exit 3
      ;;
  esac
done

if [[ -z "$CHANGE_ID" ]]; then
  echo "error: --change-id is required" >&2
  exit 3
fi

# ── Slug-guard (same regex as _SLUG_RE_BIN in bin/orchestrator) ───────────────
if ! echo "$CHANGE_ID" | grep -qE '^[a-z0-9][a-z0-9-]*$'; then
  echo "error: --change-id '$CHANGE_ID' violates slug guard (must match ^[a-z0-9][a-z0-9-]*\$)" >&2
  exit 3
fi

# ── DB path resolution (same convention as bin/orchestrator) ──────────────────
if [[ -n "${METRICS_DB:-}" ]]; then
  DB_PATH="$METRICS_DB"
elif [[ -n "${ORCHESTRATOR_HOME:-}" ]]; then
  DB_PATH="$ORCHESTRATOR_HOME/metrics.duckdb"
else
  echo "error: METRICS_DB or ORCHESTRATOR_HOME must be set" >&2
  exit 1
fi

if [[ ! -f "$DB_PATH" ]]; then
  echo "error: DB not found at $DB_PATH" >&2
  exit 1
fi

# ── Determine PYTHONPATH for orchestrator_next imports ────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# ORC-106: orchestrator_next package moved to the repo root (was config/scripts/).
ORCHESTRATOR_NEXT_PARENT="$REPO_ROOT"

# ── Setup temp directory for JSON data files ──────────────────────────────────
TMPDIR_REPORT="$(mktemp -d "${TMPDIR:-/tmp}/cost-report-XXXXXX")"
trap 'rm -rf "$TMPDIR_REPORT"' EXIT

# ── Query 1: feature_report row ───────────────────────────────────────────────
duckdb -readonly -json "$DB_PATH" \
  -c "SELECT * FROM feature_report WHERE change_id = '$CHANGE_ID'" \
  > "$TMPDIR_REPORT/feature.json"

# ── Query 2: per-model rollup (D-9: direct step_events GROUP BY model) ────────
duckdb -readonly -json "$DB_PATH" -c "
  SELECT COALESCE(model,'unknown') AS model,
         COALESCE(SUM(cost_usd),0.0) AS cost_usd,
         COALESCE(SUM(input_tokens),0) AS input_tokens,
         COALESCE(SUM(output_tokens),0) AS output_tokens,
         COUNT(*) AS step_count
  FROM step_events
  WHERE change_id = '$CHANGE_ID'
  GROUP BY model
  ORDER BY model ASC" \
  > "$TMPDIR_REPORT/per_model.json"

# ── Query 3: phase_report ─────────────────────────────────────────────────────
duckdb -readonly -json "$DB_PATH" \
  -c "SELECT phase, cost_usd, input_tokens, output_tokens, duration_ms, step_count
      FROM phase_report
      WHERE change_id = '$CHANGE_ID'
      ORDER BY first_seen ASC, phase ASC" \
  > "$TMPDIR_REPORT/phase.json"

# ── Query 4: agent_report ─────────────────────────────────────────────────────
duckdb -readonly -json "$DB_PATH" \
  -c "SELECT agent_name, cost_usd, input_tokens, output_tokens, duration_ms, step_count
      FROM agent_report
      WHERE change_id = '$CHANGE_ID'
      ORDER BY agent_name ASC" \
  > "$TMPDIR_REPORT/agent.json"

# ── Query 5: native tools ─────────────────────────────────────────────────────
duckdb -readonly -json "$DB_PATH" -c "
  SELECT tool_name,
         COUNT(*) AS calls,
         SUM(duration_ms) AS total_ms,
         AVG(duration_ms) AS avg_ms,
         MAX(duration_ms) AS max_ms
  FROM tool_calls
  WHERE change_id = '$CHANGE_ID' AND is_mcp = false
  GROUP BY tool_name
  ORDER BY calls DESC, tool_name ASC" \
  > "$TMPDIR_REPORT/native_tools.json"

# ── Query 6: MCP calls ────────────────────────────────────────────────────────
duckdb -readonly -json "$DB_PATH" -c "
  SELECT tool_name,
         COUNT(*) AS calls,
         SUM(duration_ms) AS total_ms,
         AVG(duration_ms) AS avg_ms,
         MAX(duration_ms) AS max_ms
  FROM tool_calls
  WHERE change_id = '$CHANGE_ID' AND is_mcp = true
  GROUP BY tool_name
  ORDER BY calls DESC, tool_name ASC" \
  > "$TMPDIR_REPORT/mcp_calls.json"

# ── Query 7: per-agent tool use ───────────────────────────────────────────────
duckdb -readonly -json "$DB_PATH" -c "
  SELECT agent_name, tool_name, COUNT(*) AS calls
  FROM tool_calls
  WHERE change_id = '$CHANGE_ID'
  GROUP BY agent_name, tool_name
  ORDER BY agent_name ASC, calls DESC, tool_name ASC" \
  > "$TMPDIR_REPORT/per_agent_tools.json"

# ── Query 8: feature_baseline (median delta) ─────────────────────────────────
duckdb -readonly -json "$DB_PATH" \
  -c "SELECT change_id, cost_usd, duration_ms, step_count,
             median_cost_usd, median_duration_ms, repo_feature_count
      FROM feature_baseline
      WHERE change_id = '$CHANGE_ID'" \
  > "$TMPDIR_REPORT/baseline.json" 2>/dev/null || echo "[]" > "$TMPDIR_REPORT/baseline.json"

# ── Inline python3 formatter ──────────────────────────────────────────────────
CHANGE_ID_PY="$CHANGE_ID"
DB_PATH_PY="$DB_PATH"
TMPDIR_REPORT_PY="$TMPDIR_REPORT"
ORCHESTRATOR_NEXT_PARENT_PY="$ORCHESTRATOR_NEXT_PARENT"
TAIL_MODE_PY="$TAIL_MODE"

python3 -c "
import sys, json, os

tmpdir = '$TMPDIR_REPORT_PY'
tail_mode = '$TAIL_MODE_PY' == 'true'

def load(fname):
    with open(os.path.join(tmpdir, fname)) as f:
        return json.load(f)

feature_json        = load('feature.json')
per_model_json      = load('per_model.json')
phase_json          = load('phase.json')
agent_json          = load('agent.json')
native_json         = load('native_tools.json')
mcp_json            = load('mcp_calls.json')
per_agent_tools_json = load('per_agent_tools.json')
baseline_json       = load('baseline.json')

if not feature_json:
    sys.stderr.write('error: no events for change_id=$CHANGE_ID_PY\n')
    sys.exit(1)

r = feature_json[0]
b = baseline_json[0] if baseline_json else None

# ── Formatting helpers (mirror _fmt_* from cost_report.py) ────────────────────
def fmt_usd(v):
    if v is None:
        return '\$0.0000'
    return f'\${float(v):.4f}'

def fmt_tokens(v):
    if v is None:
        return '0'
    return f'{int(v):,}'

def fmt_ms(v):
    if v is None:
        return '0ms'
    v = float(v)
    if v >= 60000:
        return f'{v / 60000:.1f}m'
    return f'{v / 1000:.1f}s'

def md_table(headers, rows):
    sep = ' | '
    header_row = sep.join(headers)
    divider = sep.join(['---'] * len(headers))
    lines_t = [f'| {header_row} |', f'| {divider} |']
    for row in rows:
        lines_t.append(f'| {sep.join(str(c) for c in row)} |')
    return '\n'.join(lines_t)

def nx_label(cost, median):
    if not median or median == 0:
        return 'n/a'
    ratio = float(cost) / float(median)
    return f'{ratio:.2f}x median'

# ── Tail mode: single summary line ────────────────────────────────────────────
if tail_mode:
    cost  = fmt_usd(r['cost_usd'])
    dur   = fmt_ms(r['duration_ms'])
    steps = r['step_count']
    nx    = nx_label(r['cost_usd'], b['median_cost_usd'] if b else None)
    print(f'$CHANGE_ID_PY: {cost} · {dur} · {steps} steps · {nx}')
    sys.exit(0)

lines = []

# 1. Executive Summary
lines.append('## Executive Summary')
lines.append('')
lines.append('| Metric | Value |')
lines.append('| --- | --- |')
lines.append(f'| Total cost | {fmt_usd(r[\"cost_usd\"])} |')
lines.append(f'| Input tokens | {fmt_tokens(r[\"input_tokens\"])} |')
lines.append(f'| Output tokens | {fmt_tokens(r[\"output_tokens\"])} |')
lines.append(f'| Duration | {fmt_ms(r[\"duration_ms\"])} |')
lines.append(f'| Steps | {r[\"step_count\"]} |')
rework_ratio = float(r['rework_ratio']) if r.get('rework_ratio') is not None else 0.0
lines.append(f'| Rework ratio | {rework_ratio:.1%} |')
lines.append('')

# 2. Median Delta
lines.append('## Median Delta')
lines.append('')
if b and b.get('median_cost_usd') and b['median_cost_usd'] > 0:
    n = b['repo_feature_count']
    lines.append(f'| Metric | This run | Repo median (n={n}) | Delta |')
    lines.append('| --- | --- | --- | --- |')
    cost_nx   = float(r['cost_usd']) / float(b['median_cost_usd'])
    dur_nx    = float(r['duration_ms']) / float(b['median_duration_ms']) if b.get('median_duration_ms') and b['median_duration_ms'] > 0 else None
    lines.append(f'| Cost    | {fmt_usd(r[\"cost_usd\"])} | {fmt_usd(b[\"median_cost_usd\"])} | {cost_nx:.2f}x |')
    lines.append(f'| Duration | {fmt_ms(r[\"duration_ms\"])} | {fmt_ms(b[\"median_duration_ms\"])} | {f\"{dur_nx:.2f}x\" if dur_nx else \"n/a\"} |')
else:
    lines.append('_Insufficient data for median comparison (need > 1 feature with cost > 0)._')
lines.append('')

# 3. Per-Phase
lines.append('## Per-Phase')
lines.append('')
if phase_json:
    headers = ['Phase', 'Cost', 'Input Tok', 'Output Tok', 'Duration', 'Steps']
    rows = [
        [
            row['phase'],
            fmt_usd(row['cost_usd']),
            fmt_tokens(row['input_tokens']),
            fmt_tokens(row['output_tokens']),
            fmt_ms(row['duration_ms']),
            str(row['step_count']),
        ]
        for row in phase_json
    ]
    lines.append(md_table(headers, rows))
else:
    lines.append('_No data._')
lines.append('')

# 4. Per-Agent
lines.append('## Per-Agent')
lines.append('')
if agent_json:
    headers = ['Agent', 'Cost', 'Input Tok', 'Output Tok', 'Duration', 'Steps']
    rows = [
        [
            row['agent_name'],
            fmt_usd(row['cost_usd']),
            fmt_tokens(row['input_tokens']),
            fmt_tokens(row['output_tokens']),
            fmt_ms(row['duration_ms']),
            str(row['step_count']),
        ]
        for row in agent_json
    ]
    lines.append(md_table(headers, rows))
else:
    lines.append('_No data._')
lines.append('')

# 5. Per-Model
lines.append('## Per-Model')
lines.append('')
if per_model_json:
    headers = ['Model', 'Cost', 'Input Tok', 'Output Tok', 'Steps']
    rows = [
        [
            row['model'],
            fmt_usd(row['cost_usd']),
            fmt_tokens(row['input_tokens']),
            fmt_tokens(row['output_tokens']),
            str(row['step_count']),
        ]
        for row in per_model_json
    ]
    lines.append(md_table(headers, rows))
else:
    lines.append('_No data._')
lines.append('')

# 6. Native Tools
lines.append('## Native Tools')
lines.append('')
if native_json:
    headers = ['Tool', 'Calls', 'Total', 'Avg', 'Max']
    rows = [
        [
            row['tool_name'],
            str(row['calls']),
            fmt_ms(row['total_ms']) if row.get('total_ms') is not None else '—',
            fmt_ms(row['avg_ms'])   if row.get('avg_ms') is not None else '—',
            fmt_ms(row['max_ms'])   if row.get('max_ms') is not None else '—',
        ]
        for row in native_json
    ]
    lines.append(md_table(headers, rows))
else:
    lines.append('_No native tool calls._')
lines.append('')

# 7. MCP Calls
lines.append('## MCP Calls')
lines.append('')
if mcp_json:
    headers = ['Tool', 'Calls', 'Total', 'Avg', 'Max']
    rows = [
        [
            row['tool_name'],
            str(row['calls']),
            fmt_ms(row['total_ms']) if row.get('total_ms') is not None else '—',
            fmt_ms(row['avg_ms'])   if row.get('avg_ms') is not None else '—',
            fmt_ms(row['max_ms'])   if row.get('max_ms') is not None else '—',
        ]
        for row in mcp_json
    ]
    lines.append(md_table(headers, rows))
else:
    lines.append('_No MCP calls._')
lines.append('')

# 8. Per-Agent Tool Use
lines.append('## Per-Agent Tool Use')
lines.append('')
if per_agent_tools_json:
    current_agent = None
    agent_rows = []
    for row in per_agent_tools_json:
        if row['agent_name'] != current_agent:
            if current_agent is not None and agent_rows:
                lines.append(md_table(['Tool', 'Calls'], agent_rows))
                lines.append('')
            current_agent = row['agent_name']
            lines.append(f'### {current_agent}')
            lines.append('')
            agent_rows = []
        agent_rows.append([row['tool_name'], str(row['calls'])])
    if agent_rows:
        lines.append(md_table(['Tool', 'Calls'], agent_rows))
        lines.append('')
else:
    lines.append('_No tool call data._')
    lines.append('')

# 9. Anomalies — import _anomalies from cost_report.py (preserved per D-1)
lines.append('## Anomalies')
lines.append('')
lines.append('### Tool not in role')
lines.append('')

sys.path.insert(0, '$ORCHESTRATOR_NEXT_PARENT_PY')
try:
    import duckdb as _duckdb
    _db = _duckdb.connect('$DB_PATH_PY', read_only=True)
    from orchestrator_next.cost_report import _anomalies, _step_allowlist_anomalies
    # Use repo_root from the feature_report row (required by _anomalies WHERE clause)
    _repo_root = r.get('repo_root', '')
    anomalies = _anomalies(_db, _repo_root, '$CHANGE_ID_PY')
    step_anomalies = _step_allowlist_anomalies(_db, _repo_root, '$CHANGE_ID_PY')
    _db.close()
except Exception as _e:
    anomalies = []
    step_anomalies = []

if anomalies:
    for a in anomalies:
        lines.append(
            f'- {a[\"agent_name\"]} used {a[\"tool_name\"]} ({a[\"calls\"]} calls)'
            f' — not in declared tools list'
        )
    lines.append('')
else:
    lines.append('_No anomalies detected._')
    lines.append('')

if step_anomalies:
    lines.append('### Tool not in step allowlist')
    lines.append('')
    for a in step_anomalies:
        lines.append(
            f'- {a[\"agent_name\"]} used {a[\"tool_name\"]} on step {a[\"step_id\"]}'
            f' ({a[\"calls\"]} calls) — not in step allowlist'
        )
    lines.append('')

print('\n'.join(lines), end='')
"
exit $?

