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
DELETE FROM features WHERE repo_root = '$q_repo_rb';
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

  duckdb "$DB" <<SQL || { failed=$((failed + 1)); continue; }
INSERT OR REPLACE INTO features (repo_root, change_id, schema, status, started_at, completed_at, payload_json)
VALUES ('$q_repo', '$q_change', '$q_schema', '$q_status', '$q_started', '$q_completed', '$q_payload');
SQL
  ingested=$((ingested + 1))
done

# ── Report ───────────────────────────────────────────────────────────────
echo "metrics: ingested=$ingested skipped=$skipped failed=$failed db=$DB"
exit 0
