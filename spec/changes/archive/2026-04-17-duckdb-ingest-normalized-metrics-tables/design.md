# Design — duckdb-ingest-normalized-metrics-tables (HL-284)

## Selected Approach

Extend `register-repo.sh` with DDL + ingest logic for three new tables (no FKs), and `metrics-query.sh` with three new named queries. Consistency is enforced by strict child-first delete ordering inside a single-writer bash script. No new files are needed beyond an added test file.

Rationale (vs. alternatives in `spec.md`): simplest design that meets the spec. Reuses the existing `sql_quote`, scope-clause, and fixture-seed patterns. Changes are additive — no existing behaviour is modified.

## Data Model (DDL)

All three tables live in the same `metrics.duckdb` alongside `features`.

```sql
CREATE TABLE IF NOT EXISTS step_history (
  repo_root     VARCHAR NOT NULL,
  change_id     VARCHAR NOT NULL,
  step_ord      INTEGER NOT NULL,     -- 0-based position in state.yaml step_history[]
  step_id       VARCHAR,
  phase         VARCHAR,
  status        VARCHAR,
  agent         VARCHAR,              -- NULL for inline steps
  started_at    VARCHAR,
  completed_at  VARCHAR,
  total_tokens  BIGINT,               -- NULL when usage block absent
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
  cost_usd      DOUBLE,               -- NULL when blocking branch not merged
  PRIMARY KEY (repo_root, change_id, step_id)
);
```

**No `FOREIGN KEY` clauses.** Rationale: DuckDB v1.5.2 FK enforcement breaks `INSERT OR REPLACE INTO features` (internal DELETE+INSERT) and lacks `ON DELETE CASCADE`. See `spec.md` NFR-1 and `discovery.md` Approach B.

**Orphan behaviour (AC-8)**: A manual `DELETE FROM features WHERE ...` executed outside `register-repo.sh` will succeed and leave child rows orphaned. Operators MUST use `register-repo.sh --rebuild` for deletion. This is the documented contract.

## Ingest Flow

### Per-feature upsert (runs once per `state.yaml`)

```
for each state_file in $ARCHIVE_GLOB:
  parse change_id, schema, status, started_at, completed_at, payload_json  # existing
  q_repo, q_change = sql_quote(REPO_ROOT), sql_quote(change_id)

  # 1. Child-first delete (consistency ordering)
  DELETE FROM step_history       WHERE repo_root = q_repo AND change_id = q_change;
  DELETE FROM per_agent_metrics  WHERE repo_root = q_repo AND change_id = q_change;
  DELETE FROM per_step_metrics   WHERE repo_root = q_repo AND change_id = q_change;

  # 2. Existing features upsert (unchanged)
  INSERT OR REPLACE INTO features (...) VALUES (...);

  # 3. Child insert — step_history (always attempted; empty array → no rows)
  for i, step in enumerate(yq '.step_history[]'):
    INSERT INTO step_history VALUES (q_repo, q_change, i, step_id, phase, status, agent, started_at, completed_at, usage.total_tokens, usage.tool_uses, usage.duration_ms);

  # 4. Child insert — per_agent_metrics (graceful-skip if key absent)
  # NOTE: metrics.per_agent_tokens is stored as a JSON-encoded STRING scalar
  # (YAML type !!str), not a YAML map. Extract as string, then parse with fromjson.
  per_agent_json = yq -r '.metrics.per_agent_tokens // ""'
  if per_agent_json is non-null and non-empty:
    for agent, m in (echo "$per_agent_json" | yq -p=json '. | to_entries[]'):
      INSERT INTO per_agent_metrics VALUES (q_repo, q_change, agent, m.total_tokens, m.cost_usd, m.tool_uses, m.duration_ms, m.steps);

  # 5. Child insert — per_step_metrics (graceful-skip if key absent)
  if yq '.metrics.per_step' is an object:
    for step_id, m in that object:
      INSERT INTO per_step_metrics VALUES (q_repo, q_change, step_id, m.total_tokens, m.tool_uses, m.duration_ms, m.cost_usd);
```

Implementation note — `yq` is invoked per-field in the existing script. For child rows we batch-emit SQL by piping `yq -o json` output through a small helper (shell loop over `yq` queries that produce `|`-delimited rows, or a single `yq` expression producing SQL literals). Preference: keep it shell-native — a loop over `yq -r '.step_history | keys | .[]'` indices producing one INSERT per row. Performance is fine at archive counts < 1000.

### `--rebuild` path (runs once per repo)

```
DELETE FROM step_history       WHERE repo_root = q_repo;
DELETE FROM per_agent_metrics  WHERE repo_root = q_repo;
DELETE FROM per_step_metrics   WHERE repo_root = q_repo;
DELETE FROM features           WHERE repo_root = q_repo;
# then proceed to archive walk as normal
```

Children deleted first (even without FKs) so that on an interrupted rebuild we don't leave feature-less orphans.

### Graceful-skip matrix

| State-file shape | step_history rows | per_agent_metrics rows | per_step_metrics rows |
|---|---|---|---|
| Full data (post-blocking-branch)        | N | M | K |
| No `metrics.per_agent_tokens`           | N | 0 | K |
| No `metrics.per_step`                   | N | M | 0 |
| `step_history[]` entry with no `usage`  | N (that row has NULLs) | M | K |
| No `step_history[]`                     | 0 | M | K |

All skips: silent, exit 0, no warning log.

## Named Query Extensions (`metrics-query.sh`)

Add three cases to the existing `case "$QUERY_ID"` block (before `*)`):

```bash
step-cost-hotspots)
  SQL="SELECT step_id, SUM(total_tokens) AS total_tokens, SUM(cost_usd) AS cost_usd, SUM(duration_ms) AS duration_ms FROM per_step_metrics WHERE ${SCOPE} GROUP BY step_id ORDER BY cost_usd DESC NULLS LAST, total_tokens DESC${LIMIT_CLAUSE}"
  ;;
agent-cost-hotspots)
  SQL="SELECT agent, SUM(total_tokens) AS total_tokens, SUM(cost_usd) AS cost_usd, SUM(tool_uses) AS tool_uses FROM per_agent_metrics WHERE ${SCOPE} GROUP BY agent ORDER BY total_tokens DESC${LIMIT_CLAUSE}"
  ;;
agent-duration-outliers)
  SQL="WITH agg AS (SELECT agent, AVG(duration_ms) AS avg_ms FROM per_agent_metrics WHERE ${SCOPE} GROUP BY agent), baseline AS (SELECT AVG(duration_ms) AS overall_avg FROM per_agent_metrics WHERE ${SCOPE}) SELECT agg.agent, agg.avg_ms, baseline.overall_avg FROM agg, baseline WHERE agg.avg_ms > 2 * baseline.overall_avg ORDER BY agg.avg_ms DESC${LIMIT_CLAUSE}"
  ;;
```

`${SCOPE}` is already `repo_root = '...'` or `1=1` from the existing scope-clause block — reused verbatim, so `--repo` and `--fleet` work unchanged.

## Testing Strategy

### `metrics-query.test.sh` (extend existing fixture)

Extend the inline fixture block (lines 138–156) to also seed:
- 2 rows into `per_step_metrics` for `REPO_A` / `feature-alpha` (e.g. step_ids `implement`, `review` with distinct cost values)
- 2 rows into `per_agent_metrics` for `REPO_A` (distinct agents, distinct durations where one is >2× the mean → outlier)
- 0 rows for `REPO_B` (covers the zero-row / nonzero-exit path)

Add assertions (follow existing `check_zero_exit`/`check_nonempty` style):
- `step-cost-hotspots --repo REPO_A` → exit 0, non-empty
- `agent-cost-hotspots --repo REPO_A` → exit 0, non-empty
- `agent-duration-outliers --repo REPO_A` → exit 0, non-empty, output contains the outlier agent
- `step-cost-hotspots --fleet` → exit 0, non-empty
- `step-cost-hotspots --repo REPO_B` (no rows) → exit non-zero, empty stdout

### `__tests__/register-repo.test.sh` (new file, TDD)

New fixture state.yaml(s) under `$TMPDIR`:
- `feature-full/state.yaml` — includes `step_history[]` with 2 steps, `metrics.per_agent_tokens` with 2 agents, `metrics.per_step` with 2 step_ids.
- `feature-partial/state.yaml` — step_history[] present, no `per_agent_tokens`, no `per_step`.
- `feature-no-usage/state.yaml` — one step_history[] entry with no `usage` block.

Assertions:
- After running `register-repo.sh`, `SELECT COUNT(*) FROM step_history WHERE change_id='feature-full'` returns 2; `per_agent_metrics` returns 2; `per_step_metrics` returns 2.
- For `feature-partial`: `step_history` > 0, `per_agent_metrics` = 0, `per_step_metrics` = 0, exit code 0 (no error on missing keys).
- For `feature-no-usage`: the single row has NULL `total_tokens`, `tool_uses`, `duration_ms`.
- Re-running ingest (second invocation) → identical row counts (idempotency).
- `--rebuild` → all three new tables empty for that repo_root before re-ingest, then identical counts to first run.

### Backfill verification (AC-6)

`verify.md` records output of:
```
register-repo.sh --rebuild $ORCHESTRATOR_HOME
duckdb $METRICS_DB "SELECT COUNT(*) FROM step_history; SELECT COUNT(*) FROM per_agent_metrics; SELECT COUNT(*) FROM per_step_metrics"
```

## Error Handling

Matches existing script ethos: non-blocking (`exit 0` on tool/parse failures). Per-state-file failures in the new ingest blocks increment the existing `failed` counter. A failure inserting one child row for one feature does not abort the loop.

## File-Level Change Summary

- `config/scripts/register-repo.sh` — +DDL block (~30 lines), +rebuild child-delete block (~10 lines), +child-row ingest inside feature loop (~60 lines). No changes to existing features upsert.
- `config/scripts/metrics-query.sh` — +3 `case` arms (~10 lines total). No changes to arg-parsing or scope/limit logic.
- `config/scripts/metrics-query.test.sh` — +fixture seed SQL (~15 lines), +new assertions (~20 lines).
- `config/scripts/__tests__/register-repo.test.sh` — NEW (~150 lines).

## Non-Functional Notes

- **Idempotency**: child-first DELETE + INSERT OR REPLACE features + child INSERT ⇒ repeated runs produce identical rows.
- **Portability**: no bash-ism beyond what the existing script already uses (`yq`, `duckdb`, associative iteration via `yq -r 'keys'`).
- **Security**: every string value flows through `sql_quote` before interpolation.
- **Observability**: existing `metrics: ingested=X skipped=Y failed=Z db=...` report line is retained; no new log lines needed unless we opt to emit per-table counts (minor enhancement — include in the report line).

## Open Decisions Closed

1. Blocking-branch timing → proceed now; AC-6 row counts expected low for `per_step_metrics`.
2. FK strategy → no FKs (Approach B).
3. Per-step speculative columns → excluded from `step_history`; present in `per_step_metrics` only (`cost_usd`, nullable).
4. `per_step_metrics` source → read-only from `metrics.per_step`; no on-the-fly computation.

## Manual Deletion Contract

`DELETE FROM features WHERE repo_root = '...' AND change_id = '...'` executed directly (outside `register-repo.sh`) will succeed without error and will NOT affect child rows. The child tables (`step_history`, `per_agent_metrics`, `per_step_metrics`) will retain orphan rows for the deleted feature.

**Operator contract:** Always use `register-repo.sh --rebuild <repo_root>` to remove a repo's data. The `--rebuild` path deletes child rows first (child-first ordering), then deletes `features`, then re-ingests — leaving the database in a consistent state.

**Verification (AC-8):** A direct `DELETE FROM features WHERE ...` outside the script leaves orphan rows in child tables as expected. This is by design and documented here. Do not add FK constraints — see `spec.md` NFR-1 for rationale.
