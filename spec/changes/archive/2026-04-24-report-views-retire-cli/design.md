# Design: Report views retire CLI — Phase 3 of workflow-engine-as-state-machine

## Context

Today three Python files and one Python-shelling shell script form the read-side projection layer over DuckDB:

- `config/scripts/orchestrator_next/metrics_report.py` (455 lines) — `aggregate_metrics()` composes `_totals` + `feature_metrics` + `feature_complexity` + per-step/per-agent/per-tool rollups into a single flat dict.
- `config/scripts/orchestrator_next/cost_report.py` (1037 lines) — `aggregate_feature`, `aggregate_by_scope`, `aggregate_repo`, `_totals`, `_per_phase`, `_per_agent`, `_per_model`, `_native_tools`, `_mcp_calls`, `_by_complexity`, `_by_step`, `_by_tool`, plus markdown renderers and anomaly detectors.
- `scripts/inline/compute-swe-metrics.sh` — calls `orchestrator metrics --format json` and YAML-wraps.
- `config/scripts/read-sub-state-metrics.sh` — calls `orchestrator metrics --format json` and projects three keys.
- `bin/orchestrator` — `_metrics_main` (lines 56–146) and `_cost_main` (lines 149–284) expose these via CLI.

Phase 1 established the DuckDB migration runner (`config/scripts/orchestrator_next/upsert.py::_run_migrations`) and the migrations directory (`config/scripts/orchestrator_next/migrations/`). Phase 2 introduced in-progress step_events rows with NULL `cost_usd`. This phase replaces the Python aggregation layer with four DuckDB views delivered via migration `0002_report_views.sql`, retires the two CLI subcommands, and rewrites the three shell consumers (`compute-swe-metrics.sh`, `read-sub-state-metrics.sh`, and a new `scripts/cost-report.sh`).

**Live-schema verification.** DDL for target tables was read from `config/scripts/orchestrator_next/upsert.py` at HEAD before drafting these view sketches:

- `step_events` columns (line 29–55): `repo_root, change_id, phase, step_id, attempt, agent_name, agent_id, status, schema_name, started_at, ended_at, duration_ms, model, input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens, cost_usd, turns, tool_calls_json, artifacts_json, escalation_json, upserted_at`
- `tool_calls` columns (line 58–72): `repo_root, change_id, phase, step_id, attempt, agent_name, tool_name, is_mcp, call_seq, called_at, duration_ms`
- `feature_complexity` columns (line 85–95): `repo_root, change_id, complexity, schema_name, started_at, completed_at, upserted_at`
- `feature_metrics` columns (line 104–139): `repo_root, change_id, schema_name, tasks_total, tasks_planned, tasks_added, tasks_completed, tasks_failed, resolve_rate, pass_at_1, pass_at_2, regressions, regression_rate, retries_total, human_interventions, files_changed, insertions, deletions, total_commits, rework_commits, rework_rate, review_scores_json, review_score_avg, wall_clock_minutes, source, computed_at`
- `pricing` columns (from `0001_seed_pricing.sql`): `model_id, input_usd, output_usd, cache_read_usd, cache_creation_usd, is_local, effective_from`

## Goals / Non-Goals

### Goals

- Four DuckDB views replace the Python aggregation layer for feature / phase / agent / repo rollups.
- CLI surface shrinks by two verbs; no new verbs introduced.
- Shell consumers continue to emit byte-identical output shape to today's versions against a frozen baseline.
- `_anomalies()` / `_step_allowlist_anomalies()` preserved as standalone Python functions for Phase 5 decision.
- Net code deletion (migration SQL ~150 lines added, ~700 Python lines + 2 CLI handlers + 1 test file deleted).

### Non-Goals

- Anomaly detection CLI or new script surface (Phase 5).
- `orchestrator done` rename or salvage path (Phase 4).
- Changes to `step_events`, `tool_calls`, `feature_metrics`, `feature_complexity`, or `pricing` DDL.
- New Python module `orchestrator_next.report` (OQ-7 resolved: do not create).
- Per-complexity or `--since` filtered repo views (no non-CLI callers).

## Approaches Considered

### Approach A: Four views via `0002_report_views.sql` + direct shell consumption (selected)

Migration file creates all four views as `CREATE OR REPLACE VIEW`. Views live alongside `0001_seed_pricing.sql` and are applied by the same `_run_migrations` runner. Shell consumers `duckdb -json -readonly` and `python3 -c` for output shaping. `scripts/cost-report.sh` handles the `/orchestrate` workflow-complete markdown emission. Stringified JSON columns (`per_agent_tokens`, etc.) encoded via `json_group_object(...)::VARCHAR` inside the view; shells' `python3 -c` re-dumps with `sort_keys=True` for determinism.

- **Pros**: zero new Python modules, follows Phase 1's pattern exactly, developer ad-hoc queries work via plain `duckdb` CLI, view surface auditable as SQL diff.
- **Cons**: driver-visible SQL in the shell scripts (mitigated: SQL is parametrised via single-statement `-c` flags with change_id already slug-guarded before reaching the shell).
- **Complexity**: **M** (view DDL + 3 shell rewrites + 1 test-file recomposition + 2 CLI handlers deleted + ~700-line Python module trimmed).

### Approach B: Views + new thin `orchestrator_next.report` module

Views as above, plus a small Python module that wraps `SELECT * FROM feature_report WHERE change_id = ?` calls. Shell consumers import and use the module.

- **Pros**: tidier test surface; Python callers need not shell out.
- **Cons**: Resurrects a Python intermediary for a one-site use. Driver (OQ-7) explicitly ruled against. Adds a second layer that every future schema change touches.
- **Complexity**: **M**.

### Approach C: Keep CLI wrappers; views are an internal implementation detail

`_cost_main` and `_metrics_main` continue to exist but read from views internally.

- **Pros**: No caller-site changes.
- **Cons**: Defeats the phase goal (D-1: retire CLI). Driver locked.
- **Complexity**: **S**.

### Selected Approach

**Approach A.** It is the simplest direct expression of the driver's locked intent: SQL views as the single source of truth, no Python wrapper, shell scripts consume directly. Matches Phase 1's `estimate-cost.sh` rewrite pattern (verified: `config/scripts/estimate-cost.sh:158–168` — `duckdb -readonly -json` + `python3 -c` is the existing idiom).

## High-Level Design

### Architecture Overview

```
 ┌──────────────────────┐
 │ 0002_report_views.sql│ ──── applied by ────► ┌───────────────────────┐
 │   CREATE OR REPLACE  │                        │ DuckDB                │
 │   VIEW feature_report│                        │   feature_report      │
 │   VIEW phase_report  │                        │   phase_report        │
 │   VIEW agent_report  │                        │   agent_report        │
 │   VIEW repo_report   │                        │   repo_report         │
 └──────────────────────┘                        │   step_events         │
                                                 │   tool_calls          │
 ┌──────────────────────┐                        │   feature_metrics     │
 │ scripts/cost-report  │ ─ duckdb -json -c ──► │   feature_complexity  │
 │   .sh (NEW)          │                        │   pricing             │
 └──────────────────────┘                        └───────────────────────┘
 ┌──────────────────────┐                                ▲
 │ compute-swe-metrics  │ ─ duckdb -json -c ─────────────┤
 │   .sh (rewrite)      │                                │
 └──────────────────────┘                                │
 ┌──────────────────────┐                                │
 │ read-sub-state-      │ ─ duckdb -json -c ─────────────┘
 │   metrics.sh (rewrite│
 └──────────────────────┘

 ┌──────────────────────┐
 │ bin/orchestrator     │ ── `metrics` / `cost` verbs REMOVED from main()
 │   (minus ~240 lines) │
 └──────────────────────┘

 ┌──────────────────────┐
 │ orchestrator_next/   │
 │   cost_report.py     │ ── trimmed to _anomalies() + _step_allowlist_anomalies()
 │   metrics_report.py  │ ── DELETED in full
 └──────────────────────┘
```

### Key Abstractions

- **Migration file `0002_report_views.sql`** — pure DDL, `CREATE OR REPLACE VIEW`. Idempotent (the migration runner still records it exactly once via `schema_migrations`, but the DDL itself is replayable because every view uses `OR REPLACE`). Uses `CREATE OR REPLACE` rather than `CREATE VIEW IF NOT EXISTS` so that subsequent phases changing view definitions can reuse the same name without a drop.
- **Shell data-flow idiom**: `json="$(duckdb -readonly -json "$DB_PATH" -c "SELECT … FROM feature_report WHERE change_id = '…'")"` followed by `echo "$json" | python3 -c "…"` for JSON→YAML reshape. Matches `config/scripts/estimate-cost.sh:158–168`.
- **Shell-layer slug-guard**: the shell scripts validate `change_id` against `^[a-z0-9][a-z0-9-]*$` before embedding in SQL (same regex as `_SLUG_RE_BIN` at `bin/orchestrator:37`). This replaces the CLI-layer validation that existed in `_metrics_main` / `_cost_main`.

## Low-Level Design

### Components

#### 1. `config/scripts/orchestrator_next/migrations/0002_report_views.sql`

Four view definitions. Full DDL (validated against the live schema in `upsert.py`):

```sql
-- Phase 3 of workflow-engine-as-state-machine.
-- Replaces metrics_report.aggregate_metrics() and cost_report._totals/_per_phase/_per_agent
-- with pure SQL views. Consumer scripts (compute-swe-metrics.sh,
-- read-sub-state-metrics.sh, cost-report.sh) read these via `duckdb -json -readonly`.

-- ---------------------------------------------------------------------------
-- 1. feature_report — one row per (repo_root, change_id)
-- ---------------------------------------------------------------------------
-- Matches aggregate_metrics() output shape (metrics-schema.md § feature schema).
-- LEFT JOIN on feature_metrics (UC-E2: missing row must not drop the feature).
-- Stringified JSON columns: per_agent_tokens, per_agent_tools, per_tool_uses, per_step.

CREATE OR REPLACE VIEW feature_report AS
WITH base AS (
  -- Totals from step_events, per change
  SELECT
    se.repo_root,
    se.change_id,
    COALESCE(SUM(se.cost_usd), 0.0)                        AS cost_usd,
    COALESCE(SUM(se.input_tokens), 0)                      AS input_tokens,
    COALESCE(SUM(se.output_tokens), 0)                     AS output_tokens,
    COALESCE(SUM(se.cache_creation_input_tokens), 0)       AS cache_creation_input_tokens,
    COALESCE(SUM(se.cache_read_input_tokens), 0)           AS cache_read_input_tokens,
    COALESCE(SUM(se.input_tokens), 0)
      + COALESCE(SUM(se.output_tokens), 0)
      + COALESCE(SUM(se.cache_creation_input_tokens), 0)   AS total_tokens,
    COALESCE(SUM(se.turns), 0)                             AS turns,
    COALESCE(SUM(se.duration_ms), 0)                       AS duration_ms,
    COUNT(*)                                               AS step_count,
    COALESCE(SUM(CASE WHEN se.attempt > 1 THEN se.cost_usd ELSE 0.0 END), 0.0) AS rework_cost,
    COALESCE(SUM(se.cost_usd), 0.0)                        AS rework_denom
  FROM step_events se
  GROUP BY se.repo_root, se.change_id
),
dom_model AS (
  -- Dominant model: max SUM(input_tokens) per change
  SELECT repo_root, change_id, model
  FROM (
    SELECT
      repo_root, change_id, model,
      SUM(input_tokens) AS input_sum,
      ROW_NUMBER() OVER (
        PARTITION BY repo_root, change_id
        ORDER BY SUM(input_tokens) DESC NULLS LAST
      ) AS rn
    FROM step_events
    WHERE model IS NOT NULL
    GROUP BY repo_root, change_id, model
  ) ranked
  WHERE rn = 1
),
priced AS (
  -- Most recent pricing row per model_id
  SELECT
    model_id,
    input_usd, output_usd, cache_read_usd, cache_creation_usd,
    ROW_NUMBER() OVER (PARTITION BY model_id ORDER BY effective_from DESC) AS rn
  FROM pricing
),
tool_count AS (
  SELECT repo_root, change_id, COUNT(*) AS tool_calls_count
  FROM tool_calls
  GROUP BY repo_root, change_id
),
per_agent_tokens_agg AS (
  -- Two-level: inner CTE aggregates per (repo_root, change_id, agent_name);
  -- outer query collapses by (repo_root, change_id) emitting one JSON with
  -- every agent as a top-level key. Mirrors per_agent_tools_agg below.
  SELECT
    repo_root, change_id,
    json_group_object(agent_name, agent_stats)::VARCHAR AS per_agent_tokens
  FROM (
    SELECT
      repo_root, change_id, agent_name,
      json_object(
        'total_tokens',  COALESCE(SUM(input_tokens),0) + COALESCE(SUM(output_tokens),0),
        'input_tokens',  COALESCE(SUM(input_tokens),0),
        'output_tokens', COALESCE(SUM(output_tokens),0),
        'cost_usd',      COALESCE(SUM(cost_usd),0.0),
        'duration_ms',   COALESCE(SUM(duration_ms),0),
        'step_count',    COUNT(*)
      ) AS agent_stats
    FROM step_events
    GROUP BY repo_root, change_id, agent_name
  ) t
  GROUP BY repo_root, change_id
),
per_agent_tools_agg AS (
  SELECT
    tc.repo_root, tc.change_id,
    json_group_object(agent_name, tool_map)::VARCHAR AS per_agent_tools
  FROM (
    SELECT
      repo_root, change_id, agent_name,
      json_group_object(tool_name, calls) AS tool_map
    FROM (
      SELECT repo_root, change_id, agent_name, tool_name, COUNT(*) AS calls
      FROM tool_calls
      GROUP BY repo_root, change_id, agent_name, tool_name
    )
    GROUP BY repo_root, change_id, agent_name
  ) tc
  GROUP BY tc.repo_root, tc.change_id
),
per_tool_uses_agg AS (
  SELECT
    repo_root, change_id,
    json_group_object(tool_name, calls)::VARCHAR AS per_tool_uses
  FROM (
    SELECT repo_root, change_id, tool_name, COUNT(*) AS calls
    FROM tool_calls
    GROUP BY repo_root, change_id, tool_name
  )
  GROUP BY repo_root, change_id
),
per_step_agg AS (
  SELECT
    se.repo_root, se.change_id,
    json_group_object(
      step_id,
      json_object(
        'total_tokens', total_tokens,
        'tool_uses',    tool_uses,
        'duration_ms',  duration_ms,
        'executions',   executions
      )
    )::VARCHAR AS per_step
  FROM (
    SELECT
      se.repo_root, se.change_id, se.step_id,
      COALESCE(SUM(se.input_tokens),0) + COALESCE(SUM(se.output_tokens),0) AS total_tokens,
      COALESCE(SUM(se.duration_ms),0) AS duration_ms,
      COUNT(*) AS executions,
      COALESCE((
        SELECT COUNT(*) FROM tool_calls tc
        WHERE tc.repo_root = se.repo_root
          AND tc.change_id = se.change_id
          AND tc.step_id   = se.step_id
      ), 0) AS tool_uses
    FROM step_events se
    GROUP BY se.repo_root, se.change_id, se.step_id
  ) se
  GROUP BY se.repo_root, se.change_id
)
SELECT
  b.repo_root,
  b.change_id,
  -- totals
  b.cost_usd,
  b.input_tokens,
  b.output_tokens,
  b.cache_creation_input_tokens,
  b.cache_read_input_tokens,
  b.total_tokens,
  b.turns,
  b.duration_ms,
  b.step_count,
  -- rework ratio (denom-zero guard)
  CASE WHEN b.rework_denom = 0 THEN 0.0
       ELSE b.rework_cost / b.rework_denom END AS rework_ratio,
  -- dominant model + pricing (LEFT JOIN; fallback __default__ applied by shell if model IS NULL)
  dm.model                                                AS model,
  COALESCE(p.input_usd,      pd.input_usd,      15.0)     AS pricing_input_usd,
  COALESCE(p.output_usd,     pd.output_usd,     75.0)     AS pricing_output_usd,
  COALESCE(p.cache_read_usd, pd.cache_read_usd,  1.5)     AS pricing_cache_read_usd,
  COALESCE(p.cache_creation_usd, pd.cache_creation_usd, 18.75) AS pricing_cache_creation_usd,
  -- gross_usd = (input + cache_create + cache_read) * input_rate / 1M + output * output_rate / 1M
  (
    (b.input_tokens + b.cache_creation_input_tokens + b.cache_read_input_tokens)
      * COALESCE(p.input_usd, pd.input_usd, 15.0) / 1000000.0
    + b.output_tokens * COALESCE(p.output_usd, pd.output_usd, 75.0) / 1000000.0
  )                                                        AS gross_usd,
  -- tool_calls_count (LEFT JOIN; zero if no tool_calls rows)
  COALESCE(tc.tool_calls_count, 0)                        AS tool_calls_count,
  -- category / complexity (LEFT JOIN both)
  COALESCE(fm.schema_name, fc.schema_name, 'feature')     AS category,
  fc.complexity                                            AS complexity,
  -- feature_metrics passthroughs (NULL-safe via LEFT JOIN)
  fm.tasks_total, fm.tasks_planned, fm.tasks_added,
  fm.tasks_completed, fm.tasks_failed,
  fm.resolve_rate, fm.pass_at_1, fm.pass_at_2,
  fm.regressions, fm.regression_rate,
  fm.retries_total, fm.human_interventions,
  fm.files_changed, fm.insertions, fm.deletions,
  fm.total_commits, fm.rework_commits, fm.rework_rate,
  fm.review_scores_json, fm.review_score_avg,
  fm.wall_clock_minutes,
  -- benchmarks with denom-zero guards
  CASE WHEN COALESCE(fm.tasks_total,0) = 0 THEN 0.0
       ELSE b.cost_usd / fm.tasks_total END                AS cost_per_task_usd,
  CASE WHEN COALESCE(fm.tasks_completed,0) = 0 THEN 0.0
       ELSE b.cost_usd / fm.tasks_completed END            AS cost_per_resolution_usd,
  CASE WHEN COALESCE(fm.tasks_total,0) = 0 THEN 0
       ELSE CAST(b.total_tokens / fm.tasks_total AS BIGINT) END AS tokens_per_task,
  CASE WHEN COALESCE(fm.tasks_completed,0) = 0 THEN 0
       ELSE CAST(b.total_tokens / fm.tasks_completed AS BIGINT) END AS tokens_per_resolution,
  CASE WHEN b.output_tokens = 0 THEN 0.0
       ELSE ROUND((b.input_tokens + b.cache_creation_input_tokens)::DOUBLE / b.output_tokens, 4) END AS input_output_ratio,
  CASE WHEN (b.input_tokens + b.cache_creation_input_tokens + b.cache_read_input_tokens) = 0 THEN 0.0
       ELSE ROUND(b.cache_read_input_tokens::DOUBLE /
                  (b.input_tokens + b.cache_creation_input_tokens + b.cache_read_input_tokens), 4) END AS cache_hit_rate,
  -- stringified JSON nested columns
  COALESCE(pat.per_agent_tokens, '{}')                     AS per_agent_tokens,
  COALESCE(pato.per_agent_tools, '{}')                     AS per_agent_tools,
  COALESCE(ptu.per_tool_uses,    '{}')                     AS per_tool_uses,
  COALESCE(ps.per_step,           '{}')                    AS per_step
FROM base b
LEFT JOIN dom_model dm
  ON dm.repo_root = b.repo_root AND dm.change_id = b.change_id
LEFT JOIN priced p
  ON p.model_id = dm.model AND p.rn = 1
LEFT JOIN priced pd
  ON pd.model_id = '__default__' AND pd.rn = 1
LEFT JOIN tool_count tc
  ON tc.repo_root = b.repo_root AND tc.change_id = b.change_id
LEFT JOIN feature_metrics fm
  ON fm.repo_root = b.repo_root AND fm.change_id = b.change_id
LEFT JOIN feature_complexity fc
  ON fc.repo_root = b.repo_root AND fc.change_id = b.change_id
LEFT JOIN per_agent_tokens_agg pat
  ON pat.repo_root = b.repo_root AND pat.change_id = b.change_id
LEFT JOIN per_agent_tools_agg pato
  ON pato.repo_root = b.repo_root AND pato.change_id = b.change_id
LEFT JOIN per_tool_uses_agg ptu
  ON ptu.repo_root = b.repo_root AND ptu.change_id = b.change_id
LEFT JOIN per_step_agg ps
  ON ps.repo_root = b.repo_root AND ps.change_id = b.change_id;


-- ---------------------------------------------------------------------------
-- 2. phase_report — one row per (repo_root, change_id, phase)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW phase_report AS
SELECT
  repo_root,
  change_id,
  phase,
  COALESCE(SUM(cost_usd), 0.0)    AS cost_usd,
  COALESCE(SUM(input_tokens), 0)  AS input_tokens,
  COALESCE(SUM(output_tokens), 0) AS output_tokens,
  COALESCE(SUM(duration_ms), 0)   AS duration_ms,
  COUNT(*)                         AS step_count,
  MIN(started_at)                  AS first_seen
FROM step_events
GROUP BY repo_root, change_id, phase
ORDER BY repo_root, change_id, first_seen ASC, phase ASC;


-- ---------------------------------------------------------------------------
-- 3. agent_report — one row per (repo_root, change_id, agent_name)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW agent_report AS
SELECT
  repo_root,
  change_id,
  agent_name,
  COALESCE(SUM(cost_usd), 0.0)    AS cost_usd,
  COALESCE(SUM(input_tokens), 0)  AS input_tokens,
  COALESCE(SUM(output_tokens), 0) AS output_tokens,
  COALESCE(SUM(duration_ms), 0)   AS duration_ms,
  COUNT(*)                         AS step_count
FROM step_events
GROUP BY repo_root, change_id, agent_name
ORDER BY repo_root, change_id, agent_name ASC;


-- ---------------------------------------------------------------------------
-- 4. repo_report — one row per (repo_basename, change_id)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW repo_report AS
SELECT
  regexp_extract(repo_root, '[^/]+$') AS repo_basename,
  change_id,
  COALESCE(SUM(cost_usd), 0.0)        AS cost_usd,
  COALESCE(SUM(input_tokens), 0)      AS input_tokens,
  COALESCE(SUM(output_tokens), 0)     AS output_tokens,
  COUNT(*)                             AS step_count,
  MIN(started_at)                      AS first_seen
FROM step_events
GROUP BY regexp_extract(repo_root, '[^/]+$'), change_id
ORDER BY repo_basename, first_seen ASC, change_id ASC;
```

**Note on `per_agent_tokens` CTE nesting.** The shell consumer re-parses and re-dumps with `json.dumps(sort_keys=True)`, so the intra-view key ordering is not byte-load-bearing. The CTE structure above is the simplest valid DuckDB form; if the developer finds the `json_group_object` nesting does not compile cleanly, the equivalent form `json_group_object(agent_name, (SELECT json_object(...) ...))` or a final `json_group_object` over a per-agent CTE both work. T-0 (migration-smoke) validates the exact DDL runs against an `in_memory_db`.

#### 2. `scripts/cost-report.sh` (new)

Shell wrapper replacing `orchestrator cost --change-id`. Structure:

```bash
#!/usr/bin/env bash
# cost-report.sh — Markdown cost summary for workflow-complete.
# Usage: cost-report.sh --change-id <cid>
# Reads: $METRICS_DB or $ORCHESTRATOR_HOME/metrics.duckdb
# Writes: 8-section markdown report to stdout
set -uo pipefail

# 1. Parse --change-id, slug-guard (^[a-z0-9][a-z0-9-]*$)
# 2. Resolve DB path (same convention as bin/orchestrator)
# 3. Two duckdb queries (feature_report row + per-model GROUP BY)
# 4. python3 -c renders the 8-section markdown from JSON
```

Slug-guard:
```bash
if ! [[ "$CHANGE_ID" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
  echo "error: --change-id '$CHANGE_ID' violates slug guard" >&2; exit 3
fi
```

Data read (two parallel `duckdb` calls for feature row and per-model rollup):
```bash
FEATURE_JSON=$(duckdb -readonly -json "$DB_PATH" \
  -c "SELECT * FROM feature_report WHERE change_id = '$CHANGE_ID'")
PER_MODEL_JSON=$(duckdb -readonly -json "$DB_PATH" -c "
  SELECT COALESCE(model,'unknown') AS model,
         COALESCE(SUM(cost_usd),0.0) AS cost_usd,
         COALESCE(SUM(input_tokens),0) AS input_tokens,
         COALESCE(SUM(output_tokens),0) AS output_tokens,
         COUNT(*) AS step_count
  FROM step_events
  WHERE change_id = '$CHANGE_ID'
  GROUP BY model
  ORDER BY model ASC")
```

Inline `python3 -c` renders the 8-section markdown: Executive Summary, Per-Phase (second `duckdb` query against `phase_report`), Per-Agent (against `agent_report`), Per-Model (from `PER_MODEL_JSON`), Native Tools / MCP Calls (from the two `tool_calls` queries), Per-Agent Tool Use (from `per_agent_tools`), Anomalies (skipped — the anomaly path remains in Python; the markdown report notes "_Anomaly detection deferred to Phase 5._" if retention is chosen). Byte-equivalence against `render_markdown_feature` is validated at T-9; if equivalent, `render_markdown_feature` is deleted; if not, the shell script `python3 -c "from orchestrator_next.cost_report import render_markdown_feature; ..."` imports the helper and uses it unchanged. **T-9 is a decision gate, not a design-time assertion.**

#### 3. `scripts/inline/compute-swe-metrics.sh` (rewrite)

New body (~30 lines; retains signature, state_dir argument, change_id read from state.yaml):

```bash
# After reading CHANGE_ID from state.yaml:
JSON=$(duckdb -readonly -json "$DB_PATH" \
  -c "SELECT * FROM feature_report WHERE change_id = '$CHANGE_ID'")

TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
echo "$JSON" | python3 -c "
import sys, json, yaml
rows = json.load(sys.stdin)
if not rows:
    sys.stderr.write('ERROR: no events for change_id=$CHANGE_ID\n'); sys.exit(1)
r = rows[0]
# Reshape to the nested dict that aggregate_metrics() produced.
# Every stringified-JSON column is re-parsed and re-dumped with sort_keys for determinism (D-6).
per_agent_tokens = json.dumps(json.loads(r['per_agent_tokens']), sort_keys=True)
per_agent_tools  = json.dumps(json.loads(r['per_agent_tools']),  sort_keys=True)
per_tool_uses    = json.dumps(json.loads(r['per_tool_uses']),    sort_keys=True)
per_step_dict    = json.loads(r['per_step'])
# Review scores: parse review_scores_json (string) → list
review_scores = []
if r.get('review_scores_json'):
    try: review_scores = json.loads(r['review_scores_json'])
    except Exception: review_scores = []
metrics = {
    'tokens': {
        'input':          r['input_tokens'],
        'output':         r['output_tokens'],
        'cache_creation': r['cache_creation_input_tokens'],
        'cache_read':     r['cache_read_input_tokens'],
        'total':          r['total_tokens'],
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
    'turns':            r['turns'],
    'api_calls':        r['turns'],
    'tool_calls':       r['tool_calls_count'],
    'wall_clock_minutes': r['wall_clock_minutes'],
    'category':         r['category'],
    'human_interventions': r['human_interventions'],
    'rework_commits':   r['rework_commits'],
    'rework_rate':      r['rework_rate'],
    'resolution': {k: r[k] for k in ('tasks_total','tasks_planned','tasks_added','tasks_completed','tasks_failed','resolve_rate','pass_at_1','pass_at_2','regressions','regression_rate')},
    'retries': {'total': r['retries_total']},
    'churn':   {k: r[k] for k in ('files_changed','insertions','deletions','total_commits')},
    'review_scores':     review_scores,
    'review_score_avg':  r['review_score_avg'],
    'lint_delta':        0,
    'benchmarks': {k: r[k] for k in ('cost_per_task_usd','cost_per_resolution_usd','tokens_per_task','tokens_per_resolution','input_output_ratio','cache_hit_rate')},
    'per_agent_tokens':  per_agent_tokens,
    'per_agent_tools':   per_agent_tools,
    'per_tool_uses':     per_tool_uses,
    'per_step':          per_step_dict,
    'source':            'duckdb@$TS',
}
print(yaml.safe_dump({'metrics': metrics}, sort_keys=True, default_flow_style=False), end='')
"
```

The output YAML shape is `metrics: {tokens:{...}, cost:{...}, …, per_step: {<step_id>: {...}}, source: 'duckdb@<ts>'}` — identical to today's `aggregate_metrics()` output wrapped under `metrics:`.

#### 4. `config/scripts/read-sub-state-metrics.sh` (rewrite)

```bash
# Inputs: slug argument
# Slug-guard (same regex as compute-swe-metrics.sh)
JSON=$(duckdb -readonly -json "$DB_PATH" \
  -c "SELECT total_tokens, duration_ms, files_changed
      FROM feature_report WHERE change_id = '$SLUG'")
echo "$JSON" | python3 -c "
import sys, json
rows = json.load(sys.stdin)
if not rows:
    sys.stderr.write('ERROR: no events for slug=$SLUG\n'); sys.exit(1)
r = rows[0]
tok = r.get('total_tokens') or 0
dur = r.get('duration_ms') or 0
churn = r.get('files_changed') or 0
print(f'metrics:\n  tokens:\n    total: {tok}\n  duration_ms: {dur}\n  churn:\n    files_changed: {churn}')
"
```

#### 5. `bin/orchestrator` (deletion)

- Delete `_metrics_main` (lines 56–146).
- Delete `_cost_main` (lines 149–284).
- Update `_usage()` (lines 40–53) — remove the two `metrics` / `cost` usage lines.
- Update `main()` verb dispatch (lines 566–580) — remove `"cost"`, `"metrics"` from the recognised-verb set, delete the two branches.

#### 6. `config/scripts/orchestrator_next/cost_report.py` (trim)

Retain only:
- `_load_contract`, `ContractError`, `StepContract` imports (used by `_step_allowlist_anomalies`).
- `load_agent_tools` import (used by `_anomalies`).
- `_anomalies()` function (lines 333–361).
- `_step_allowlist_anomalies()` function (lines 364–411).
- Module docstring (updated to reflect the post-trim surface).

Delete everything else (`_fmt_usd`, `_fmt_tokens`, `_fmt_ms`, `_load_pricing_for_model`, `_step_events_columns`, `_totals`, `_per_phase`, `_per_agent`, `_per_model`, `_native_tools`, `_mcp_calls`, `_per_agent_tools`, `_by_complexity`, `_by_step`, `_by_agent_scope`, `_by_tool`, `aggregate_feature`, `aggregate_by_scope`, `aggregate_repo`, `_md_table`, `render_markdown_feature`, `render_markdown_scoped`, `render_markdown_repo`, `render_json`).

**T-9 gate override**: if inline markdown formatting in `scripts/cost-report.sh` diverges from `render_markdown_feature`, `render_markdown_feature` + `_md_table` + `_fmt_*` helpers are retained; other functions are deleted regardless.

#### 7. `config/scripts/orchestrator_next/metrics_report.py` (delete)

Deleted in full. No caller survives.

#### 8. `config/scripts/tests/test_cost_cli.py` (delete)

Deleted in full. The test surface it provided is replaced by:
- `config/scripts/orchestrator_next/tests/test_report_views.py` — SQL-level assertions over the views.
- `config/scripts/__tests__/cost-report.test.sh` — end-to-end shell test for the new wrapper.

### Data Flow

Workflow-complete (UC-1):
```
/orchestrate SKILL → scripts/cost-report.sh --change-id $CID
  → duckdb -json feature_report (+ step_events GROUP BY model)
  → python3 -c markdown formatter
  → stdout captured and included in driver's final message.
```

Compute-swe-metrics (UC-2):
```
complete/compute-swe-metrics inline step → compute-swe-metrics.sh $STATE_DIR
  → read change_id from state.yaml
  → duckdb -json feature_report
  → python3 -c reshape + yaml.safe_dump
  → stdout parsed by driver → injected under metrics: in state.yaml.
```

Autopilot sampling (UC-3):
```
autopilot D.5 → read-sub-state-metrics.sh $SLUG
  → duckdb -json feature_report (3 columns only)
  → python3 -c narrow YAML
  → stdout merged by autopilot-session-rollup.sh.
```

### State Management

No new runtime state. The four views are idempotent `CREATE OR REPLACE` DDL tracked by `schema_migrations` via the existing runner (`_run_migrations` in `upsert.py`). Views are recomputed on every query — no materialisation. Archived feature state.yaml files retain their historical `metrics:` blocks unchanged (no data migration).

### Error Handling

- **Empty result for `change_id`** (no events): shell scripts print `ERROR: no events for …` to stderr and exit 1. Matches pre-phase behaviour.
- **Read-only DB without migration**: the migration runner does not run on read-only connections (`duckdb -readonly`). The four views must therefore be present before any `-readonly` consumer opens the DB. This is guaranteed because `ensure_schema()` — which calls `_run_migrations()` — is invoked by every RW path (`bin/orchestrator next`, `record`, `ingest-*`, `doctor`), so any DB that has ever been written to is migrated. If a consumer opens a never-written DB read-only, `SELECT * FROM feature_report` returns `Catalog Error: View with name feature_report does not exist`; shell scripts surface this via duckdb's non-zero exit.
- **`feature_metrics` missing row** (UC-E2): LEFT JOIN returns NULLs for resolution/churn/reviews columns; `feature_report` still produces one row per `(repo_root, change_id)` found in `step_events`.
- **NULL `cost_usd`** (UC-E1): every aggregation uses `COALESCE(SUM(cost_usd), 0.0)`; in-progress rows contribute 0 to the totals.
- **Dominant model NULL**: the LEFT JOIN against `priced p` yields NULL pricing; the final `COALESCE(p.*, pd.*, 15.0/75.0/…)` falls through `__default__` pricing (matching `_load_pricing_for_model`'s fallback).
- **Missing `pricing` table** (should never happen at this phase — 0001 migration seeds it): `COALESCE(..., 15.0)` etc. provide the same conservative defaults as the Python fallback.
- **Byte-equivalence regressions at T-9**: if the inline markdown formatter does not match `render_markdown_feature` byte-for-byte against the baseline, the shell script imports `render_markdown_feature` from the trimmed `cost_report.py` instead of inlining the formatter. Decision recorded in a task-level comment in `scripts/cost-report.sh`.

## Zero-Division Translation Table

Mapping every zero-division guard in `metrics_report.py` and `cost_report.py` to its SQL form in `0002_report_views.sql`:

| Python site | SQL in `feature_report` |
|---|---|
| `_safe_div(net_usd, tasks_total)` | `CASE WHEN COALESCE(fm.tasks_total,0)=0 THEN 0.0 ELSE b.cost_usd / fm.tasks_total END` |
| `_safe_div(net_usd, tasks_completed)` | `CASE WHEN COALESCE(fm.tasks_completed,0)=0 THEN 0.0 ELSE b.cost_usd / fm.tasks_completed END` |
| `_safe_div(total_tokens, tasks_total)` | `CASE WHEN COALESCE(fm.tasks_total,0)=0 THEN 0 ELSE CAST(b.total_tokens / fm.tasks_total AS BIGINT) END` |
| `_safe_div(total_tokens, tasks_completed)` | same, over `tasks_completed` |
| `_safe_div(input+cache_create, output)` (input_output_ratio) | `CASE WHEN b.output_tokens=0 THEN 0.0 ELSE ROUND((b.input_tokens+b.cache_creation_input_tokens)::DOUBLE/b.output_tokens, 4) END` |
| `_safe_div(cache_read, input+cache_create+cache_read)` (cache_hit_rate) | `CASE WHEN (sum)=0 THEN 0.0 ELSE ROUND(b.cache_read_input_tokens::DOUBLE/(sum), 4) END` |
| rework_ratio `numerator/denominator if denominator else 0.0` | `CASE WHEN b.rework_denom=0 THEN 0.0 ELSE b.rework_cost / b.rework_denom END` |

All seven guards translated; no Python-only paths remain.

## File-Modification Table

| File | Action | Notes |
|---|---|---|
| `config/scripts/orchestrator_next/migrations/0002_report_views.sql` | **CREATE** | Four `CREATE OR REPLACE VIEW` statements. |
| `config/scripts/orchestrator_next/tests/test_report_views.py` | **CREATE** | SQL-level aggregation tests. Uses `in_memory_db` pattern. |
| `config/scripts/__tests__/cost-report.test.sh` | **CREATE** | End-to-end test for the new shell wrapper. |
| `config/scripts/__tests__/fixtures/baseline_compute_swe_metrics.yaml` | **CREATE** | Frozen stdout of pre-phase `compute-swe-metrics.sh` against the reconstructed baseline DB. |
| `config/scripts/__tests__/fixtures/baseline_read_sub_state_metrics.yaml` | **CREATE** | Frozen stdout of pre-phase `read-sub-state-metrics.sh`. |
| `config/scripts/__tests__/fixtures/baseline.duckdb.sql` | **CREATE** | Deterministic SQL dump of the baseline DB (generated from archived state's step_history). Checked in for reproducibility; easier to diff than a binary `.duckdb` file. |
| `scripts/cost-report.sh` | **CREATE** | New shell wrapper (bash 3.2). |
| `scripts/inline/compute-swe-metrics.sh` | **REWRITE** | New body queries `feature_report` via `duckdb -json -readonly`. |
| `config/scripts/read-sub-state-metrics.sh` | **REWRITE** | Narrow projection from `feature_report`. |
| `config/scripts/__tests__/compute-swe-metrics-projection.test.sh` | **REWRITE** | Assertions now include byte-equivalence against baseline fixture. |
| `config/scripts/__tests__/read-sub-state-metrics.test.sh` | **REWRITE** | Same. |
| `skills/orchestrate/SKILL.md` | **EDIT** | Replace `orchestrator cost --change-id` invocation (lines 97–102) with `scripts/cost-report.sh --change-id $CHANGE_ID`. |
| `bin/orchestrator` | **EDIT** | Delete `_metrics_main`, `_cost_main`; trim `_usage()`; remove `cost`/`metrics` from `main()` verb dispatch. |
| `config/scripts/orchestrator_next/cost_report.py` | **TRIM** | Retain only `_anomalies`, `_step_allowlist_anomalies`, imports, docstring. Possibly also `render_markdown_feature` + helpers if T-9 gate retains them. |
| `config/scripts/orchestrator_next/metrics_report.py` | **DELETE** | Entire file. |
| `config/scripts/tests/test_cost_cli.py` | **DELETE** | Entire file. CLI gone, tests have no target. |
| `config/tests/test-orchestrator-metrics-json-shape.sh` | **DELETE** | Tests the retired CLI. No replacement. |
| `config/tests/test-metrics-pipeline-integration.sh` | **EDIT** or **DELETE** | Depends on whether it still references `orchestrator metrics`; audit at T-11. |

## OQ Resolutions (reference)

- **OQ-1 (anomalies)** → D-1 Approach C. Preserve `_anomalies` / `_step_allowlist_anomalies` as standalone Python functions. Phase 5 decides fate.
- **OQ-2 (markdown renderers)** → D-2. Inline `python3 -c` formatter in `scripts/cost-report.sh`. Retain `render_markdown_feature` only if inline path fails byte-equivalence at T-9.
- **OQ-3 (aggregate_repo variants)** → D-3. Drop `--since` / `--by complexity`.
- **OQ-4 (read-sub-state-metrics.sh)** → D-4. In scope, with its own byte-equivalence fixture.
- **OQ-5 (baseline)** → D-5. Replay `durable-intent-and-resume` archived state.
- **OQ-6 (JSON encoding)** → D-6. `json_group_object(...)::VARCHAR` in views, `json.dumps(sort_keys=True)` in shells for determinism.
- **OQ-7 (orchestrator_next.report)** → D-7. Do not create.
- **New D-8 (per_step location)** → stringified JSON column in `feature_report`.
- **New D-9 (per_model location)** → direct `step_events GROUP BY model` inside `scripts/cost-report.sh`; no view.

## Constraints

- Bash 3.2 compatible (no `declare -A`, `mapfile`, `readarray`, `${var^^}`).
- SQL parametrisation: `change_id` slug-guarded in shell before interpolation into `-c` flag.
- DuckDB version: same as Phase 1 (project pinned; no version change introduced by this phase).
- Migration immutability: once `0002_report_views.sql` is merged and applied in any archive, its contents must not change. Phase 4+ adds `0003_*.sql` for any view adjustments.

## Trade-offs

- **Accepted**: SQL-in-shell-strings is ergonomically slightly worse than a Python wrapper, but the three callers each use one small query and the driver explicitly ruled out a wrapper module (OQ-7).
- **Accepted**: `feature_report` is a wide view (~40 columns including stringified JSON). Query plans include multiple `LEFT JOIN`s and several CTEs. Verified at T-13 gate against a production-shaped DB that `change_id`-filtered queries do not regress 2× wall-clock vs. `orchestrator cost` today.
- **Accepted**: Anomaly detection is orphaned from any invocation point during this phase. Phase 5 decides whether to resurrect.
- **Rejected**: A Python helper module (OQ-7). Would reduce shell-SQL surface but creates two sources of truth.

## Decisions

- **DV-1 (migration file name)** → `0002_report_views.sql`. Lexical ordering after `0001_seed_pricing.sql`. → Consequence: `_run_migrations` applies it in the correct order on every RW connection.
- **DV-2 (`CREATE OR REPLACE`)** → All four views use `CREATE OR REPLACE VIEW`. → Consequence: subsequent phases can redefine without a separate DROP. The migration runner still records the file-name as applied exactly once.
- **DV-3 (stringified JSON columns)** → `per_agent_tokens`, `per_agent_tools`, `per_tool_uses`, `per_step` are emitted as `VARCHAR` containing a JSON object. Shells re-dump with `sort_keys=True`. → Consequence: deterministic output; `yq -p=json` in `register-repo.sh` continues to work unchanged.
- **DV-4 (LEFT JOIN on feature_metrics / feature_complexity)** → Never INNER. → Consequence: features that only have step_events still appear in `feature_report` (UC-E2).
- **DV-5 (baseline via replay)** → T-4 reconstructs the baseline DB by replaying archived state's step_history through `upsert_step_event`, then runs the pre-phase shells and checks in their stdout. → Consequence: baseline is reproducible from git-tracked inputs (archived state + pre-phase scripts), not a binary DB blob.
- **DV-6 (T-9 as decision gate)** → Byte-equivalence of inline markdown formatter vs `render_markdown_feature` is a runtime check executed inside task T-9. If equivalent, the formatter inlines and `render_markdown_feature` is deleted. If not, the formatter imports and `render_markdown_feature` is retained. → Consequence: no up-front design commitment to either path.
- **DV-7 (no-new-verbs honoured)** → `bin/orchestrator`'s verb set shrinks by exactly two. → Consequence: end-state `next` / `done` is one phase closer.

## Open Questions

None for this phase. All seven discovery OQs resolved under § OQ Resolutions. New DV-1..DV-7 above are design-time decisions, not open questions.

<!-- Format contract: contracts/artifact-formats.md § Design Format Contract -->
