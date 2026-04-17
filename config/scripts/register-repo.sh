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
duckdb "$DB" <<'SQL'
CREATE TABLE IF NOT EXISTS features (
  repo_root      VARCHAR NOT NULL,
  change_id      VARCHAR NOT NULL,
  schema         VARCHAR,
  status         VARCHAR,
  started_at     VARCHAR,
  completed_at   VARCHAR,
  payload_json   VARCHAR,
  ingested_at    TIMESTAMP DEFAULT current_timestamp,
  PRIMARY KEY (repo_root, change_id)
);

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
  PRIMARY KEY (repo_root, change_id, step_ord)
);

CREATE TABLE IF NOT EXISTS per_agent_metrics (
  repo_root     VARCHAR NOT NULL,
  change_id     VARCHAR NOT NULL,
  agent         VARCHAR NOT NULL,
  total_tokens  BIGINT,
  cost_usd      DOUBLE,
  tool_uses     INTEGER,
  duration_ms   BIGINT,
  steps         INTEGER,
  PRIMARY KEY (repo_root, change_id, agent)
);

CREATE TABLE IF NOT EXISTS per_step_metrics (
  repo_root     VARCHAR NOT NULL,
  change_id     VARCHAR NOT NULL,
  step_id       VARCHAR NOT NULL,
  total_tokens  BIGINT,
  tool_uses     INTEGER,
  duration_ms   BIGINT,
  cost_usd      DOUBLE,
  PRIMARY KEY (repo_root, change_id, step_id)
);
SQL

# ── Optional rebuild ─────────────────────────────────────────────────────
if [[ "$REBUILD" == true ]]; then
  q_repo_rb="${REPO_ROOT//\'/\'\'}"
  duckdb "$DB" <<SQL
DELETE FROM step_history      WHERE repo_root = '$q_repo_rb';
DELETE FROM per_agent_metrics WHERE repo_root = '$q_repo_rb';
DELETE FROM per_step_metrics  WHERE repo_root = '$q_repo_rb';
DELETE FROM features          WHERE repo_root = '$q_repo_rb';
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
    skipped=$((skipped + 1))
    continue
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

  # 1. Child-first delete for (repo_root, change_id)
  duckdb "$DB" <<SQL || { failed=$((failed + 1)); continue; }
DELETE FROM step_history      WHERE repo_root = '$q_repo' AND change_id = '$q_change';
DELETE FROM per_agent_metrics WHERE repo_root = '$q_repo' AND change_id = '$q_change';
DELETE FROM per_step_metrics  WHERE repo_root = '$q_repo' AND change_id = '$q_change';
SQL

  # 2. Existing features upsert
  duckdb "$DB" <<SQL || { failed=$((failed + 1)); continue; }
INSERT OR REPLACE INTO features (repo_root, change_id, schema, status, started_at, completed_at, payload_json)
VALUES ('$q_repo', '$q_change', '$q_schema', '$q_status', '$q_started', '$q_completed', '$q_payload');
SQL

  # 3. Child insert — step_history
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

      duckdb "$DB" <<SQL 2>/dev/null || true
INSERT INTO step_history (repo_root, change_id, step_ord, step_id, phase, status, agent, started_at, completed_at, total_tokens, tool_uses, duration_ms)
VALUES ('$q_repo', '$q_change', $step_ord, $SH_STEP_ID, $SH_PHASE, $SH_STATUS, $SH_AGENT, $SH_STARTED, $SH_COMPLETED, $SH_TOKENS, $SH_TOOLS, $SH_DURATION);
SQL
    done
  fi

  # 4. Child insert — per_agent_metrics (per_agent_tokens is a JSON-string scalar)
  per_agent_json=$(yq -r '.metrics.per_agent_tokens // ""' "$state_file" 2>/dev/null || echo "")
  if [[ -n "$per_agent_json" && "$per_agent_json" != "null" ]]; then
    while IFS= read -r agent_key; do
      [[ -z "$agent_key" ]] && continue
      pa_tokens=$(echo "$per_agent_json" | yq -p=json -r ".[\"$agent_key\"].total_tokens // null" 2>/dev/null || echo "null")
      pa_cost=$(  echo "$per_agent_json" | yq -p=json -r ".[\"$agent_key\"].cost_usd      // null" 2>/dev/null || echo "null")
      pa_tools=$( echo "$per_agent_json" | yq -p=json -r ".[\"$agent_key\"].tool_uses     // null" 2>/dev/null || echo "null")
      pa_dur=$(   echo "$per_agent_json" | yq -p=json -r ".[\"$agent_key\"].duration_ms   // null" 2>/dev/null || echo "null")
      pa_steps=$( echo "$per_agent_json" | yq -p=json -r ".[\"$agent_key\"].steps         // null" 2>/dev/null || echo "null")

      q_agent=$(sql_quote "$agent_key")
      PA_TOKENS=$(  [[ "$pa_tokens" == "null" ]] && echo "NULL" || echo "$pa_tokens")
      PA_COST=$(    [[ "$pa_cost"   == "null" ]] && echo "NULL" || echo "$pa_cost")
      PA_TOOLS=$(   [[ "$pa_tools"  == "null" ]] && echo "NULL" || echo "$pa_tools")
      PA_DUR=$(     [[ "$pa_dur"    == "null" ]] && echo "NULL" || echo "$pa_dur")
      PA_STEPS=$(   [[ "$pa_steps"  == "null" ]] && echo "NULL" || echo "$pa_steps")

      duckdb "$DB" <<SQL 2>/dev/null || true
INSERT INTO per_agent_metrics (repo_root, change_id, agent, total_tokens, cost_usd, tool_uses, duration_ms, steps)
VALUES ('$q_repo', '$q_change', '$q_agent', $PA_TOKENS, $PA_COST, $PA_TOOLS, $PA_DUR, $PA_STEPS);
SQL
    done < <(echo "$per_agent_json" | yq -p=json -r 'keys | .[]' 2>/dev/null)
  fi

  # 5. Child insert — per_step_metrics (per_step is a YAML map)
  per_step_type=$(yq '.metrics.per_step | type' "$state_file" 2>/dev/null || echo "")
  if [[ "$per_step_type" == "!!map" ]]; then
    while IFS= read -r ps_step_id; do
      [[ -z "$ps_step_id" ]] && continue
      ps_tokens=$(yq -r ".metrics.per_step[\"$ps_step_id\"].total_tokens // null" "$state_file" 2>/dev/null || echo "null")
      ps_tools=$(  yq -r ".metrics.per_step[\"$ps_step_id\"].tool_uses     // null" "$state_file" 2>/dev/null || echo "null")
      ps_dur=$(    yq -r ".metrics.per_step[\"$ps_step_id\"].duration_ms   // null" "$state_file" 2>/dev/null || echo "null")
      ps_cost=$(   yq -r ".metrics.per_step[\"$ps_step_id\"].cost_usd      // null" "$state_file" 2>/dev/null || echo "null")

      q_ps_id=$(sql_quote "$ps_step_id")
      PS_TOKENS=$(  [[ "$ps_tokens" == "null" ]] && echo "NULL" || echo "$ps_tokens")
      PS_TOOLS=$(   [[ "$ps_tools"  == "null" ]] && echo "NULL" || echo "$ps_tools")
      PS_DUR=$(     [[ "$ps_dur"    == "null" ]] && echo "NULL" || echo "$ps_dur")
      PS_COST=$(    [[ "$ps_cost"   == "null" ]] && echo "NULL" || echo "$ps_cost")

      duckdb "$DB" <<SQL 2>/dev/null || true
INSERT INTO per_step_metrics (repo_root, change_id, step_id, total_tokens, tool_uses, duration_ms, cost_usd)
VALUES ('$q_repo', '$q_change', '$q_ps_id', $PS_TOKENS, $PS_TOOLS, $PS_DUR, $PS_COST);
SQL
    done < <(yq -r '.metrics.per_step | keys | .[]' "$state_file" 2>/dev/null)
  fi

  ingested=$((ingested + 1))
done

# ── Report ───────────────────────────────────────────────────────────────
echo "metrics: ingested=$ingested skipped=$skipped failed=$failed db=$DB"
exit 0
