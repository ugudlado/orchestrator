# DuckDB: normalized tables for step_history + per_agent + per_step metrics

Once `metrics-capture-and-workflow-streamlining` lands, every feature's state.yaml contains complete per-step and per-agent usage data. This ticket makes that data a first-class durable asset in DuckDB — not something read via `json_extract(payload_json, ...)` on every query.

## Problem

Currently `features.payload_json` is the only column that holds metrics detail. Every consumer — `/learn`, `/telemetry`, ad-hoc SQL, future dashboards — pays the `json_extract` tax on every query. Worse, `payload_json` is a convenience blob, not durable truth:

- If disk-cleanup ever trims or drops that column (realistic at fleet scale), historical metrics vanish silently
- No foreign key joins to `features`; can't easily ask "show me cost per step across all refactor features last quarter"
- JSON extraction is slow on large fleets
- External tools (notebooks, dashboards connecting to DuckDB directly) expect typed columns, not JSON paths

## Proposal

Add three normalized tables populated at ingest time by `register-repo.sh`. `payload_json` stays for now as fallback for non-normalized fields (spec text, prediction accuracy details, review findings) but is no longer the only source for metrics.

### Tables

```sql
CREATE TABLE IF NOT EXISTS step_history (
  repo_root     VARCHAR NOT NULL,
  change_id     VARCHAR NOT NULL,
  step_ord      INTEGER NOT NULL,          -- position in step_history array (0-indexed)
  step_id       VARCHAR NOT NULL,
  phase         VARCHAR,
  status        VARCHAR,
  agent         VARCHAR,                    -- NULL for inline steps
  runtime_agent VARCHAR,                    -- set on compatibility fallback
  started_at    VARCHAR,
  completed_at  VARCHAR,
  duration_ms   BIGINT,
  tool_uses     BIGINT,
  tools_json    VARCHAR,                    -- {"Read": 5, "Bash": 2, ...}
  total_tokens  BIGINT,
  input_tokens  BIGINT,
  output_tokens BIGINT,
  cost_usd      DOUBLE,
  retry_round   INTEGER,                    -- NULL unless a retry
  ingested_at   TIMESTAMP DEFAULT(current_timestamp),
  PRIMARY KEY (repo_root, change_id, step_ord),
  FOREIGN KEY (repo_root, change_id) REFERENCES features(repo_root, change_id)
);

CREATE TABLE IF NOT EXISTS per_agent_metrics (
  repo_root    VARCHAR NOT NULL,
  change_id    VARCHAR NOT NULL,
  agent        VARCHAR NOT NULL,
  total_tokens BIGINT,
  cost_usd     DOUBLE,
  duration_ms  BIGINT,
  tool_uses    BIGINT,
  step_count   INTEGER,
  ingested_at  TIMESTAMP DEFAULT(current_timestamp),
  PRIMARY KEY (repo_root, change_id, agent),
  FOREIGN KEY (repo_root, change_id) REFERENCES features(repo_root, change_id)
);

CREATE TABLE IF NOT EXISTS per_step_metrics (
  repo_root    VARCHAR NOT NULL,
  change_id    VARCHAR NOT NULL,
  step_id      VARCHAR NOT NULL,
  total_tokens BIGINT,
  cost_usd     DOUBLE,
  duration_ms  BIGINT,
  tool_uses    BIGINT,
  exec_count   INTEGER,
  ingested_at  TIMESTAMP DEFAULT(current_timestamp),
  PRIMARY KEY (repo_root, change_id, step_id),
  FOREIGN KEY (repo_root, change_id) REFERENCES features(repo_root, change_id)
);
```

Tool breakdowns stay as JSON in `step_history.tools_json`. A per-tool table (one row per step+tool) is over-normalization until we need `SELECT tool_name, SUM(count)` queries. Revisit if that pattern emerges.

### Ingest logic

`register-repo.sh` already parses state.yaml and upserts into `features`. For each archived state.yaml being ingested:

1. Upsert `features` row (unchanged — still includes `payload_json`)
2. **New**: DELETE rows in `step_history` WHERE `(repo_root, change_id)` matches → INSERT one row per entry in `step_history[]`, indexed by `step_ord`
3. **New**: DELETE rows in `per_agent_metrics` WHERE match → INSERT from `metrics.per_agent_tokens` map
4. **New**: DELETE rows in `per_step_metrics` WHERE match → INSERT from `metrics.per_step` map

DELETE+INSERT pattern makes re-ingest idempotent. Same input → same rows.

### Named queries

Add to `metrics-query.sh`:
- `step-cost-hotspots` — `SELECT step_id, SUM(cost_usd) FROM per_step_metrics GROUP BY 1 ORDER BY 2 DESC LIMIT :limit`
- `agent-cost-hotspots` — same pattern on `per_agent_metrics`
- `agent-duration-outliers` — agents with `AVG(duration_ms) > 2 * (fleet median)`
- `step-retry-hotspots` (optional) — pull from `step_history.retry_round` counts

Existing 5 named queries (cost-trend, quality-trend, cycle-count, recent-features, retry-hotspots) continue to work — they read from `features` table, which is unchanged.

### Backfill

After this ticket ships, run `register-repo.sh --rebuild` against `/Users/spidey/code/orchestrator`. The 13+ existing archived features populate `step_history` / `per_agent_metrics` / `per_step_metrics` tables from their state.yaml data.

Features archived before Task C of the blocking ticket won't have per-step or per-agent coverage for inline steps — that's expected. They're historical and missing data; backfill captures what's there.

## Scope

**In-scope:**
- `config/scripts/register-repo.sh` — table DDL at top (idempotent `CREATE TABLE IF NOT EXISTS`); ingest logic for 3 new tables
- `config/scripts/metrics-query.sh` — 3 new named queries against new tables; keep existing 5 untouched
- `config/scripts/metrics-query.test.sh` — fixture DB populates new tables; tests for new queries
- `config/scripts/__tests__/register-repo.test.sh` — new fixture asserting ingest populates the 3 tables correctly
- Backfill: `register-repo.sh --rebuild` run against orchestrator repo; row counts + feature count documented in PR

**Out-of-scope:**
- Agent tool changes
- state.yaml schema changes (handled by blocking ticket)
- Dropping `payload_json` column (separate follow-up once all consumers migrate)
- Normalizing spec/design/review text fields into tables (separate follow-up)
- Materialized-view fallback (we went straight to tables for durability)

## Acceptance criteria

- AC-1: `register-repo.sh` creates `step_history`, `per_agent_metrics`, `per_step_metrics` via `CREATE TABLE IF NOT EXISTS`; repeated runs are idempotent
- AC-2: Each feature ingest populates the three new tables; re-ingest produces identical row counts and values
- AC-3: Queries work without `json_extract`: `SELECT agent, total_tokens FROM per_agent_metrics WHERE change_id = 'X'` returns one row per agent
- AC-4: `metrics-query.sh` supports `step-cost-hotspots`, `agent-cost-hotspots`, `agent-duration-outliers` backed by new tables
- AC-5: All existing metrics-query.sh tests still pass (27+); new tests added for new queries + table population
- AC-6: Backfill run populates the 3 tables for all 13+ existing archived features; row counts documented in PR
- AC-7: `metrics-query.sh --fleet step-cost-hotspots` returns cross-repo aggregation once multiple repos are registered
- AC-8: Foreign keys enforce: DELETE of a `features` row cascades or fails predictably (document which); no orphan rows

## Dependencies

**Blocked by**: `metrics-capture-and-workflow-streamlining` — that ticket defines the state.yaml schema these tables ingest.

This ticket ships after that one lands. The backfill in AC-6 exercises both together.

## Priority

Medium-high — without this, the new per-step / per-agent data lives only in JSON blobs and gets used inconsistently. With it, we have a queryable fleet-wide cost and performance dataset that /learn and /telemetry can exploit without the JSON-parse tax.

Estimate: ~5-7 tasks. Smaller scope than the blocking ticket since the schema decisions are made.
