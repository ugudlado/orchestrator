# /learn and /telemetry Read From metrics.duckdb

## Idea

Both `/learn` and `/telemetry` were designed before the cross-repo metrics
index existed. They still glob `spec/changes/archive/*/state.yaml` and parse
YAML inline. Now that `$ORCHESTRATOR_HOME/metrics.duckdb` exists (shipped in
`cross-repo-metrics-duckdb`), both consumers should query the DuckDB index
via `duckdb -csv` for cross-repo aggregations.

This is the **producer-consumer pairing** that closes the loop: the index
exists, the consumers haven't caught up, so the metrics work currently
constrains itself to single-repo learning.

## Concrete consumption points to migrate

### `skills/learn/SKILL.md`

| Site | Current | Target |
|------|---------|--------|
| §2b cross-feature retry analysis | `ls spec/changes/archive/*/state.yaml` (last 10, this repo) | `SELECT step_id, retry_reason, COUNT(*) FROM step_history GROUP BY ... HAVING count >= 3` (cross-repo) |
| §5 cycle counter | `ls spec/changes/archive/*/state.yaml \| wc -l` | `SELECT COUNT(*) FROM features WHERE repo_root = ?` |
| §5 metrics write | append `.claude/metrics.jsonl` | also/instead `INSERT INTO cycles ...` (design call) |
| §5b rule effectiveness | scan `step_retries[step_id]` from this repo's last state.yaml | `SELECT step_id, SUM(retries) FROM step_history WHERE feature_id = ?` |
| §5c adaptive quality bar | `ls -t ...archive/*/state.yaml \| head -5` extract review_score | `SELECT AVG(review_score_avg), AVG(retry_rate) FROM features ORDER BY archived_at DESC LIMIT 5` |
| Metadata cycle count | `ls spec/changes/archive/*/state.yaml \| wc -l` | DuckDB count query |

### `skills/telemetry/SKILL.md`

| Site | Current | Target |
|------|---------|--------|
| Source list | `spec/changes/archive/*/state.yaml` + `$WORKFLOW_STATE_DIR/*/state.yaml` | DuckDB for archived; YAML still for active (DB is post-archive only) |
| `recent` mode | last 5 archived files by mtime | `... ORDER BY archived_at DESC LIMIT 5` |
| `all` mode | full glob | full table scan |
| Cross-feature aggregation | inline awk/yq across files | SQL GROUP BY |

## Scope

- Add `duckdb -csv` query helpers (small wrappers in `config/scripts/metrics-query.sh`)
  so `/learn` and `/telemetry` don't write SQL inline
- Migrate all 10 consumption points listed above to query DuckDB when
  `metrics.duckdb` exists; **fall back to file-glob otherwise** (newly-cloned
  orchestrator install before bootstrap runs)
- Cross-repo by default; per-repo filter via `WHERE repo_root = ?` for
  per-repo telemetry
- Cycle metrics in §5 may move to a new `cycles` table OR stay in
  `.claude/metrics.jsonl` — design call (lean toward DuckDB so they're
  queryable alongside features)

## Out of scope

- Restructuring the `features` table schema (consumers query the existing one)
- Reader UI / web dashboard (text output stays for now)
- Migration script for old `.claude/metrics.jsonl` data (cycles can backfill on next /learn run)
- Changes to `register-repo.sh` (producer is correct as-is; this is consumer-only work)

## Why Now

1. The producer (`cross-repo-metrics-duckdb`) just shipped with verified
   end-to-end working DuckDB writes including correct cost data (after the
   `compute-swe-metrics` cost-zero bugfix in commit `a6a2e95`).
2. The estimate_vs_actual learning loop has been training on `cost: $0` for
   every archive — fixing the cost bug exposed how much the consumers
   depend on accurate aggregates. SQL queries make the dependency obvious
   and easier to validate.
3. **Self-improvement compounds**: once `/learn` queries cross-repo retry
   patterns, the workflow-improver gets fleet-wide signal instead of
   single-repo signal — a 10x increase in learning surface area as more
   repos onboard.

## Acceptance criteria sketch

- `/learn` §2b emits cross-repo systemic retry patterns (queries `step_history` joined to `features` by `feature_id`)
- `/learn` §5c queries DuckDB for last-5-feature avg review score; falls back to file-glob if DB absent
- `/telemetry recent` reads from DuckDB; output unchanged for backward compat
- `/telemetry all` reads from DuckDB
- `config/scripts/metrics-query.sh` exposes named queries (e.g. `cost-trend`, `retry-hotspots`, `cycle-count`) with `--repo` filter and `--limit N`
- All migrations preserve existing output formats — no breaking change for callers
- Adding a 2nd repo to the registry → `/telemetry` shows fleet view; `/telemetry --repo=X` shows per-repo

## Supersedes

This ticket replaces the older single-purpose tickets:
- `learn-uses-duckdb-index/` (covered by §learn migrations above)
- `telemetry-dashboard-real/` (covered by §telemetry migrations above; the
  "real dashboard" output prototype from that ticket can be implemented as
  formatted views over the new SQL queries)

Both old tickets to be marked superseded — either deleted or kept as
pointers to this one. Recommend delete.

## Priority

- User value: 7/10 (visible improvement to /telemetry, smarter /learn)
- Strategic fit: 9/10 (closes producer-consumer loop, unblocks fleet learning)
- Technical leverage: 8/10 (one ticket fixes both consumers, reuses the just-shipped DB schema)
- Effort: medium (SQL is mechanical; fallback logic is the real complexity)
- **Score: 8.0**
