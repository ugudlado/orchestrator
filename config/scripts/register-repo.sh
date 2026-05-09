#!/usr/bin/env bash
# register-repo.sh — Append repo to metrics-registry.yaml and ingest archive
# state.yaml files into metrics.duckdb. Non-blocking on tool/parse errors.
#
# Usage:
#   register-repo.sh [--dry-run] [--rebuild] [REPO_ROOT]
#
# Environment:
#   ORCHESTRATOR_HOME  Required. Path to the orchestrator repo (DB + registry live here).
#   REPO_ROOT          Optional. Defaults to git rev-parse --show-toplevel or first positional arg.
#   METRICS_DB         Optional. Override DB path (default: $ORCHESTRATOR_HOME/metrics.duckdb).
#                      Use METRICS_DB="$TMPDIR/test.duckdb" to isolate test runs.

set -uo pipefail

# ── Parse flags ──────────────────────────────────────────────────────────
DRY_RUN=false
REBUILD=false
POSITIONAL_REPO=""

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    --rebuild) REBUILD=true ;;
    -*) echo "warn: unknown flag: $arg" >&2 ;;
    *)  POSITIONAL_REPO="$arg" ;;
  esac
done

# ── Preflight ────────────────────────────────────────────────────────────
ORCHESTRATOR_HOME="${ORCHESTRATOR_HOME:?ORCHESTRATOR_HOME must be set}"

if [[ -n "$POSITIONAL_REPO" ]]; then
  REPO_ROOT="$POSITIONAL_REPO"
else
  REPO_ROOT="${REPO_ROOT:-$(git -C "$(pwd)" rev-parse --show-toplevel 2>/dev/null || echo "")}"
fi

if [[ -z "$REPO_ROOT" ]]; then
  echo "skip: cannot determine REPO_ROOT (not a git repo and REPO_ROOT not set)" >&2
  exit 0
fi

REGISTRY="$ORCHESTRATOR_HOME/metrics-registry.yaml"
DB="${METRICS_DB:-$ORCHESTRATOR_HOME/metrics.duckdb}"

if [[ "$DRY_RUN" == true ]]; then
  ARCHIVE_COUNT=0
  ARCHIVE_GLOB="$REPO_ROOT/spec/changes/archive/*/state.yaml"
  for f in $ARCHIVE_GLOB; do
    [[ -f "$f" ]] && ARCHIVE_COUNT=$((ARCHIVE_COUNT + 1))
  done
  echo "dry-run: registry=$REGISTRY"
  echo "dry-run: db=$DB"
  echo "dry-run: repo_root=$REPO_ROOT"
  echo "dry-run: archive_count=$ARCHIVE_COUNT"
  exit 0
fi

# Tool preflight — exit 0 (non-blocking) if tools are missing
command -v yq    >/dev/null 2>&1 || { echo "skip: yq not installed";    exit 0; }
command -v duckdb >/dev/null 2>&1 || { echo "skip: duckdb not installed"; exit 0; }

# ── Register repo (idempotent) ───────────────────────────────────────────
if [[ ! -f "$REGISTRY" ]]; then
  printf '# Cross-repo metrics registry\nrepos:\n' > "$REGISTRY"
fi
if grep -Fxq -- "  - $REPO_ROOT" "$REGISTRY"; then
  echo "registry: already registered $REPO_ROOT"
else
  printf '  - %s\n' "$REPO_ROOT" >> "$REGISTRY"
  echo "registry: appended $REPO_ROOT"
fi

# ── Ensure schema ────────────────────────────────────────────────────────
# features, per_agent_metrics, per_step_metrics are views (migration 0005).
# step_history is a legacy YAML-ingest table kept for old archives.
duckdb "$DB" <<'SQL'
CREATE TABLE IF NOT EXISTS step_history (
  repo_root     VARCHAR NOT NULL,
  change_id     VARCHAR NOT NULL,
  step_ord      INTEGER NOT NULL,
  step_id       VARCHAR,
  phase         VARCHAR,
  status        VARCHAR,
  agent         VARCHAR,
  started_at    VARCHAR,
  completed_at  VARCHAR,
  total_tokens  BIGINT,
  tool_uses     INTEGER,
  duration_ms   BIGINT,
  tools_json    VARCHAR,
  PRIMARY KEY (repo_root, change_id, step_ord)
);

CREATE TABLE IF NOT EXISTS per_agent_tool_uses (
  repo_root  VARCHAR NOT NULL,
  change_id  VARCHAR NOT NULL,
  agent      VARCHAR NOT NULL,
  tool_name  VARCHAR NOT NULL,
  uses       INTEGER,
  PRIMARY KEY (repo_root, change_id, agent, tool_name)
);

CREATE TABLE IF NOT EXISTS per_tool_uses (
  repo_root  VARCHAR NOT NULL,
  change_id  VARCHAR NOT NULL,
  tool_name  VARCHAR NOT NULL,
  uses       INTEGER,
  PRIMARY KEY (repo_root, change_id, tool_name)
);

CREATE TABLE IF NOT EXISTS agent_pricing (
  agent             VARCHAR PRIMARY KEY,
  model             VARCHAR,
  backend           VARCHAR,
  input_per_1m      DOUBLE,
  output_per_1m     DOUBLE,
  cache_read_per_1m DOUBLE
);

INSERT OR REPLACE INTO agent_pricing VALUES
  ('architect',         'claude-opus-4-7',   'native_opus',    15.00, 75.00, 1.50),
  ('ideator',           'claude-opus-4-7',   'native_opus',    15.00, 75.00, 1.50),
  ('reviewer',          'claude-sonnet-4-6', 'native_sonnet',   3.00, 15.00, 0.30),
  ('developer',         'claude-sonnet-4-6', 'native_sonnet',   3.00, 15.00, 0.30),
  ('discoverer',        'claude-sonnet-4-6', 'native_sonnet',   3.00, 15.00, 0.30),
  ('workflow-improver', 'claude-sonnet-4-6', 'native_sonnet',   3.00, 15.00, 0.30),
  ('sonnet-agent',      'claude-sonnet-4-6', 'native_sonnet',   3.00, 15.00, 0.30),
  ('haiku-agent',       'claude-sonnet-4-6', 'native_sonnet',   3.00, 15.00, 0.30);
SQL

# ── Optional rebuild ─────────────────────────────────────────────────────
if [[ "$REBUILD" == true ]]; then
  q_repo_rb="${REPO_ROOT//\'/\'\'}"
  duckdb "$DB" <<SQL
DELETE FROM step_history        WHERE repo_root = '$q_repo_rb';
DELETE FROM per_agent_tool_uses WHERE repo_root = '$q_repo_rb';
DELETE FROM per_tool_uses       WHERE repo_root = '$q_repo_rb';
SQL
  echo "rebuild: deleted existing rows for $REPO_ROOT"
fi

# ── sql_quote helper ─────────────────────────────────────────────────────
# Doubles single-quotes for safe interpolation into SQL string literals.
sql_quote() { printf "%s" "${1//\'/\'\'}"; }

# ── Walk archive + ingest ────────────────────────────────────────────────
ARCHIVE_GLOB="$REPO_ROOT/spec/changes/archive/*/state.yaml"
ingested=0
skipped=0
failed=0

for state_file in $ARCHIVE_GLOB; do
  [[ -f "$state_file" ]] || continue

  change_id=$(yq -r '.change_id // ""' "$state_file" 2>/dev/null) || {
    failed=$((failed + 1))
    echo "warn: parse failed (change_id) $state_file" >&2
    continue
  }

  if [[ -z "$change_id" ]]; then
    change_id=$(basename "$(dirname "$state_file")")
    echo "warn: change_id absent, using dirname fallback: $change_id" >&2
  fi

  # Slug guard: reject change_ids with characters outside ^[a-z0-9._-]+$
  if [[ ! "$change_id" =~ ^[a-z0-9._-]+$ ]]; then
    echo "skip: change_id has unsafe chars: $change_id" >&2
    skipped=$((skipped + 1))
    continue
  fi

  json=$(yq -o json '.' "$state_file" 2>/dev/null) || {
    failed=$((failed + 1))
    echo "warn: parse failed (json) $state_file" >&2
    continue
  }

  schema=$(yq    -r '.schema      // ""' "$state_file" 2>/dev/null || echo "")
  status=$(yq    -r '.status      // ""' "$state_file" 2>/dev/null || echo "")
  started=$(yq   -r '.started_at  // ""' "$state_file" 2>/dev/null || echo "")
  completed=$(yq -r '.completed_at // ""' "$state_file" 2>/dev/null || echo "")

  q_repo=$(sql_quote "$REPO_ROOT")
  q_change=$(sql_quote "$change_id")
  q_schema=$(sql_quote "$schema")
  q_status=$(sql_quote "$status")
  q_started=$(sql_quote "$started")
  q_completed=$(sql_quote "$completed")
  q_payload=$(sql_quote "$json")

  # 1. Delete old step_history rows before reinserting (features/per_agent/per_step are views)
  duckdb "$DB" <<SQL || { failed=$((failed + 1)); continue; }
DELETE FROM step_history WHERE repo_root = '$q_repo' AND change_id = '$q_change';
SQL

  # 2. step_history insert
  step_count=$(yq '.step_history | length' "$state_file" 2>/dev/null || echo "0")
  if [[ "$step_count" =~ ^[0-9]+$ && "$step_count" -gt 0 ]]; then
    for step_ord in $(seq 0 $((step_count - 1))); do
      step_id_val=$(yq     -r ".step_history[$step_ord].step_id       // null" "$state_file" 2>/dev/null || echo "null")
      phase_val=$(yq       -r ".step_history[$step_ord].phase         // null" "$state_file" 2>/dev/null || echo "null")
      step_status_val=$(yq -r ".step_history[$step_ord].status        // null" "$state_file" 2>/dev/null || echo "null")
      agent_val=$(yq       -r ".step_history[$step_ord].agent         // null" "$state_file" 2>/dev/null || echo "null")
      s_started_val=$(yq   -r ".step_history[$step_ord].started_at    // null" "$state_file" 2>/dev/null || echo "null")
      s_completed_val=$(yq -r ".step_history[$step_ord].completed_at  // null" "$state_file" 2>/dev/null || echo "null")
      total_tokens_val=$(yq -r ".step_history[$step_ord].usage.total_tokens // null" "$state_file" 2>/dev/null || echo "null")
      tool_uses_val=$(yq    -r ".step_history[$step_ord].usage.tool_uses    // null" "$state_file" 2>/dev/null || echo "null")
      duration_ms_val=$(yq  -r ".step_history[$step_ord].usage.duration_ms  // null" "$state_file" 2>/dev/null || echo "null")
      tools_val=$(yq        -o json ".step_history[$step_ord].usage.tools // null" "$state_file" 2>/dev/null || echo "null")

      # Build SQL literals: NULL for null, quoted string for others
      sql_lit() {
        local v="$1"
        if [[ "$v" == "null" ]]; then echo "NULL"; else printf "'%s'" "$(sql_quote "$v")"; fi
      }

      SH_STEP_ID="$(sql_lit "$step_id_val")"
      SH_PHASE="$(sql_lit "$phase_val")"
      SH_STATUS="$(sql_lit "$step_status_val")"
      SH_AGENT="$(sql_lit "$agent_val")"
      SH_STARTED="$(sql_lit "$s_started_val")"
      SH_COMPLETED="$(sql_lit "$s_completed_val")"
      # Numeric: NULL as unquoted, otherwise bare integer
      SH_TOKENS=$(  [[ "$total_tokens_val" == "null" ]] && echo "NULL" || echo "$total_tokens_val")
      SH_TOOLS=$(   [[ "$tool_uses_val"    == "null" ]] && echo "NULL" || echo "$tool_uses_val")
      SH_DURATION=$([[ "$duration_ms_val" == "null" ]] && echo "NULL" || echo "$duration_ms_val")
      SH_TOOLS_JSON="$(sql_lit "$tools_val")"

      # Silent-failure guard (FR-11): reject rows where a real agent ran but
      # reported no tokens. Such rows corrupt aggregate metrics. Skip with warning.
      if [[ "$agent_val" != "null" && "$agent_val" != "inline" \
         && "$step_status_val" == "completed" \
         && "$total_tokens_val" == "null" ]]; then
        echo "WARN: skipping step_history row with missing usage: change=$q_change agent=$agent_val step=$step_id_val" >&2
        continue
      fi

      duckdb "$DB" <<SQL 2>/dev/null || true
INSERT INTO step_history (repo_root, change_id, step_ord, step_id, phase, status, agent, started_at, completed_at, total_tokens, tool_uses, duration_ms, tools_json)
VALUES ('$q_repo', '$q_change', $step_ord, $SH_STEP_ID, $SH_PHASE, $SH_STATUS, $SH_AGENT, $SH_STARTED, $SH_COMPLETED, $SH_TOKENS, $SH_TOOLS, $SH_DURATION, $SH_TOOLS_JSON);
SQL
    done
  fi

  # 4. per_agent_tool_uses (per_agent_tools is a JSON-string scalar)
  per_agent_tools_json=$(yq -r '.metrics.per_agent_tools // ""' "$state_file" 2>/dev/null || echo "")
  if [[ -n "$per_agent_tools_json" && "$per_agent_tools_json" != "null" && "$per_agent_tools_json" != "{}" ]]; then
    while IFS= read -r agent_key; do
      [[ -z "$agent_key" ]] && continue
      q_agent=$(sql_quote "$agent_key")
      while IFS= read -r tool_key; do
        [[ -z "$tool_key" ]] && continue
        pt_uses=$(echo "$per_agent_tools_json" | yq -p=json -r ".[\"$agent_key\"][\"$tool_key\"] // null" 2>/dev/null || echo "null")
        [[ "$pt_uses" == "null" ]] && continue
        q_tool=$(sql_quote "$tool_key")
        duckdb "$DB" <<SQL 2>/dev/null || true
INSERT INTO per_agent_tool_uses (repo_root, change_id, agent, tool_name, uses)
VALUES ('$q_repo', '$q_change', '$q_agent', '$q_tool', $pt_uses);
SQL
      done < <(echo "$per_agent_tools_json" | yq -p=json -r ".[\"$agent_key\"] | keys | .[]" 2>/dev/null)
    done < <(echo "$per_agent_tools_json" | yq -p=json -r 'keys | .[]' 2>/dev/null)
  fi

  # 4c. Child insert — per_tool_uses (per_tool_uses is a JSON-string scalar from JSONL)
  per_tool_json=$(yq -r '.metrics.per_tool_uses // ""' "$state_file" 2>/dev/null || echo "")
  if [[ -n "$per_tool_json" && "$per_tool_json" != "null" && "$per_tool_json" != "{}" ]]; then
    while IFS= read -r tool_key; do
      [[ -z "$tool_key" ]] && continue
      pt_uses=$(echo "$per_tool_json" | yq -p=json -r ".[\"$tool_key\"] // null" 2>/dev/null || echo "null")
      [[ "$pt_uses" == "null" ]] && continue
      q_tool=$(sql_quote "$tool_key")
      duckdb "$DB" <<SQL 2>/dev/null || true
INSERT INTO per_tool_uses (repo_root, change_id, tool_name, uses)
VALUES ('$q_repo', '$q_change', '$q_tool', $pt_uses);
SQL
    done < <(echo "$per_tool_json" | yq -p=json -r 'keys | .[]' 2>/dev/null)
  fi

  ingested=$((ingested + 1))
done

# ── Report ───────────────────────────────────────────────────────────────
echo "metrics: ingested=$ingested skipped=$skipped failed=$failed db=$DB"
exit 0
