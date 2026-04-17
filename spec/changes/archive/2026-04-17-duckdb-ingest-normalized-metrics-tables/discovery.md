---
feature-id: duckdb-ingest-normalized-metrics-tables
linear-ticket: HL-286
---

# Discovery Brief — duckdb-ingest-normalized-metrics-tables (HL-284)

## What I Understand

The underlying goal is to make per-step and per-agent usage metrics first-class queryable assets in DuckDB — typed columns with direct SQL access — rather than JSON paths extracted from `payload_json` on every query. This enables `/learn`, `/telemetry`, and future dashboards to answer questions like "which step costs the most across all refactor features?" without a `json_extract` tax.

The stated mechanism: add three normalized tables (`step_history`, `per_agent_metrics`, `per_step_metrics`) populated at ingest time inside the existing `register-repo.sh` loop, and add three named queries to `metrics-query.sh`.

## What Already Exists

### Codebase

**`config/scripts/register-repo.sh`** (the file to extend):
- Creates one table: `features (repo_root, change_id, schema, status, started_at, completed_at, payload_json, ingested_at)` with PK `(repo_root, change_id)`
- Upsert pattern: `INSERT OR REPLACE INTO features` (idempotent)
- `--rebuild` flag: `DELETE FROM features WHERE repo_root = ?` then re-ingest from scratch
- `sql_quote` helper doubles single-quotes; slug guard rejects unsafe `change_id` values
- Non-blocking on tool/parse errors (exit 0 throughout)

**`config/scripts/metrics-query.sh`** (the file to extend):
- 5 named queries: `cost-trend`, `quality-trend`, `retry-hotspots`, `cycle-count`, `recent-features`
- All 5 read only from the `features` table via `json_extract(payload_json, ...)`
- Supports `--repo`, `--fleet`, `--limit` flags; scope clause pattern established

**`config/scripts/metrics-query.test.sh`** (the test file to extend):
- 27 test assertions using fixture DB seeded in the test itself; tests all 5 named queries plus fleet/filter/error paths
- Test DB seeded via inline SQL; `METRICS_DB` env override isolates test from production

**`config/scripts/__tests__/register-repo.test.sh`**: Does NOT exist. The task description calls it "existing tests" — this is a phantom. It must be created from scratch.

**`config/scripts/__tests__/`**: Contains only `compute-swe-metrics.test.sh` + `fixtures/`. No register-repo tests.

### Data Shape in Archives

`step_history[]` entries in current state.yaml files have:
```yaml
- step_id: explore
  phase: specify
  status: completed
  agent: discoverer
  started_at: "2026-04-11T20:38:15Z"
  completed_at: "2026-04-11T20:42:20Z"
  usage:
    total_tokens: 63904
    tool_uses: 44
    duration_ms: 242864
```

Inline steps (no agent) have `step_id`, `phase`, `status`, `started_at`, `completed_at` — no `usage` block. There is no `input_tokens`, `output_tokens`, or `cost_usd` per step in any current archive. Those columns in the proposed schema will be NULL for all backfill rows.

`metrics.per_agent_tokens` is a JSON string on `features.payload_json`:
```json
{"reviewer": {"total_tokens": 102412, "cost_usd": 0.0, "tool_uses": 36, "duration_ms": 208491, "steps": 3}, ...}
```
Only 3 of 15 archives on main have `per_agent_tokens`. The rest have no per-agent data at all.

`metrics.per_step` does NOT exist in any archive on main. See blocking dependency finding below.

### External

DuckDB v1.5.2 is installed. No external libraries needed — this is pure schema and shell script extension.

## Build or Reuse?

**Extend existing scripts.** There is no alternative: `register-repo.sh` already owns the ingest loop and upsert pattern. `metrics-query.sh` already owns the named-query dispatch. The only decision is how to extend them.

## Critical Finding: Blocking Dependency NOT Merged

The feature description states "once `metrics-capture-and-workflow-streamlining` lands, every feature's state.yaml contains complete per-step and per-agent usage data."

**That branch has NOT been merged to main.** Git history shows commit `54833fe` only added spec archive documents. The actual implementation — specifically the `per_step` emission in `compute-swe-metrics.sh` — lives only on `feature/metrics-capture-and-workflow-streamlining`.

Consequence:
- Zero archives on main have `metrics.per_step`
- Only 3 of 15 archives have `per_agent_tokens`
- Backfill for AC-6 will populate `step_history` from raw `step_history[]` entries (the YAML array), and `per_agent_metrics` from `per_agent_tokens` where present
- `per_step_metrics` table will be populated from `metrics.per_step` once the blocking branch merges; backfill may need a second run post-merge

**Open question for architect**: Should the ingest logic gracefully skip absent data (per_step_metrics rows = 0 until blocking branch merges) or should this ticket be blocked until that branch is merged first?

## Critical Finding: FK + Upsert Incompatibility

DuckDB v1.5.2 enforces foreign keys. Tested:
- Inserting a child row with no matching parent row → `Constraint Error`
- Deleting a parent row that has child references → `Constraint Error: key still referenced`
- No `ON DELETE CASCADE` support

The current ingest pattern has two incompatibilities with FK-referencing child tables:

1. **`INSERT OR REPLACE INTO features`** — DuckDB implements this as DELETE + INSERT internally. If child rows reference the features row, the implicit DELETE will fail.
2. **`DELETE FROM features WHERE repo_root = ?`** (the `--rebuild` path) — will fail if any of the three new tables have rows for that repo.

Three approaches exist for the architect to choose between:

**Approach A (Child-first delete order)**
- Keep `INSERT OR REPLACE` on `features`; switch to explicit DELETE+INSERT on features during rebuild by deleting child tables first
- During per-row upsert: DELETE child rows for `(repo_root, change_id)`, then `INSERT OR REPLACE` features, then INSERT child rows
- FK enforcement provides integrity at the cost of ordering discipline
- Effort: medium — rebuild and upsert logic must be reordered

**Approach B (No FKs, application-enforced consistency)**
- Remove `FOREIGN KEY` constraints from new table DDL
- Ingest logic ensures consistency: DELETE+INSERT child rows at the same time as features
- Simpler shell script logic; no ordering constraints
- Dangling orphan rows are possible if ingest is interrupted mid-way (unlikely in practice for single-DB writes)
- Effort: small

**Approach C (Track upsert as UPDATE-or-INSERT, no implicit DELETE)**
- Replace `INSERT OR REPLACE` with explicit `INSERT INTO features ... ON CONFLICT (repo_root, change_id) DO UPDATE SET ...`
- Keep FKs; no implicit DELETE on features row, so children are safe during update
- Rebuild path still needs child-first DELETE
- DuckDB supports `INSERT OR REPLACE` and `ON CONFLICT DO UPDATE` syntax
- Effort: medium — rebuild still needs child ordering, but per-row upsert is cleaner

## Recommendation

**Approach B** (no FKs) for implementation simplicity. The integrity goal of FKs — "no orphan rows" — is fully achievable by always doing `DELETE child WHERE (repo_root, change_id)` before `INSERT OR REPLACE features`. For a single-writer bash script, application-level ordering is sufficient. DuckDB FK limitations (no cascade) make FK enforcement actively harmful here without meaningful safety benefit.

If the architect disagrees and wants FK enforcement for external query tools, Approach A is viable with the child-first ordering rule documented explicitly.

## Personas

- **P1: Workflow operator** — runs `register-repo.sh --rebuild` after deploying new features or backfilling
- **P2: Learn/telemetry agent** — queries `per_agent_metrics` and `per_step_metrics` via `metrics-query.sh` to compute cost profiles
- **P3: Dashboard user** — connects to `metrics.duckdb` directly and runs typed SQL against named tables

## Use Cases

**UC-1: Ingest archived feature** — register-repo.sh processes a state.yaml and populates all four tables. `step_history` gets one row per step array entry (indexed by `step_ord`). `per_agent_metrics` gets one row per agent key in `per_agent_tokens`. `per_step_metrics` gets one row per step_id in `metrics.per_step` (if present).

**UC-2: Re-ingest with rebuild** — operator runs `--rebuild`. Script deletes child rows first (step_history, per_agent_metrics, per_step_metrics for that repo), then deletes features rows, then re-ingests all archives. Result is identical to a fresh ingest.

**UC-3: Query step cost hotspots** — agent calls `metrics-query.sh step-cost-hotspots --repo /path/to/repo`. Returns `step_id, SUM(cost_usd)` from `per_step_metrics` ordered by cost DESC. No `json_extract` involved.

**UC-4: Query agent duration outliers** — agent calls `metrics-query.sh agent-duration-outliers --fleet`. Returns agents with `AVG(duration_ms) > 2 * fleet median` from `per_agent_metrics`.

**UC-E1: Missing per_step data** — state.yaml has no `metrics.per_step` key (pre-blocking-branch archive). Script skips per_step_metrics population for that change_id; logs a skip count. No error.

**UC-E2: Missing per_agent_tokens** — older archive has no `per_agent_tokens` in metrics. Script skips per_agent_metrics population. Existing features rows still ingested correctly.

**UC-E3: Inline step with no usage block** — step_history entry has no `usage` key. Row inserted with NULL for `total_tokens`, `tool_uses`, `duration_ms`.

## Scope

**In-scope:**
- `config/scripts/register-repo.sh` — DDL for 3 new tables; ingest logic for step_history, per_agent_metrics, per_step_metrics; rebuild ordering fix
- `config/scripts/metrics-query.sh` — 3 new named queries: `step-cost-hotspots`, `agent-cost-hotspots`, `agent-duration-outliers`
- `config/scripts/metrics-query.test.sh` — fixture updated with new tables; tests for 3 new queries
- `config/scripts/__tests__/register-repo.test.sh` — NEW FILE; tests that ingest populates 3 tables correctly
- Backfill: `--rebuild` run against orchestrator repo; row counts documented

**Out-of-scope:**
- Adding `input_tokens`, `output_tokens`, `cost_usd` per-step to state.yaml format (blocking ticket's concern)
- Dropping `payload_json` column
- Normalizing spec/design/review text fields
- FK enforcement with cascade (DuckDB limitation; approach B chosen)
- Any agent skill or step contract changes

## UI Direction

N/A — no UI involved.

## Technical Context

- DuckDB v1.5.2 (`v1.5.2 (Variegata) 8a5851971f`)
- `config/scripts/register-repo.sh` — current ingest logic; extend after line 97 (post-rebuild DELETE)
- `config/scripts/metrics-query.sh` — case block ends at line 96; add 3 new cases before `*)`
- `config/scripts/metrics-query.test.sh` — fixture DB seeded at line 138; tests from line 164
- Archive count: 15 directories under `spec/changes/archive/`; 3 have `per_agent_tokens`; 0 have `per_step`
- `per_agent_tokens` shape: JSON string keyed by agent name → `{total_tokens, cost_usd, tool_uses, duration_ms, steps}`
- `step_history[]` shape: each entry has `step_id`, `phase`, `status`, `agent?`, `started_at?`, `completed_at?`, `usage.{total_tokens?, tool_uses?, duration_ms?}`
- Blocking branch: `feature/metrics-capture-and-workflow-streamlining` — adds `per_step` to `compute-swe-metrics.sh` output; not yet merged

## Key Decisions (Architect)

**Chosen direction**: Minimal Normalized Extension — no FKs, graceful-skip on missing data, proceed now without waiting for blocking branch. Complexity: S.

**Decisions on open questions**:

1. **Blocking branch timing** — Proceed now. Ingest gracefully skips missing `metrics.per_step` and `per_agent_tokens`. Post-merge, operator reruns `register-repo.sh --rebuild` to populate per_step rows. AC-6 row counts are acknowledged to be low until the blocking branch merges; this is expected behavior, not a defect.

2. **FK strategy** — Approach B (no FKs). Application-enforced ordering: for each `(repo_root, change_id)`, `DELETE FROM <child>` before `INSERT OR REPLACE INTO features`, then `INSERT INTO <child>`. Rebuild path: delete all child rows for the repo first, then delete features rows, then re-ingest. Rationale: DuckDB FKs lack cascade, break `INSERT OR REPLACE`, and provide no safety benefit for a single-writer bash ingest.

3. **Per-step cost columns** — Drop `input_tokens`/`output_tokens` from `step_history`. `step_history` columns: `(repo_root, change_id, step_ord, step_id, phase, status, agent, started_at, completed_at, total_tokens, tool_uses, duration_ms)`. `per_step_metrics` columns: `(repo_root, change_id, step_id, total_tokens, tool_uses, duration_ms, cost_usd)` with cost_usd nullable. Rationale: schema reflects actual data shape; no speculative columns.

4. **per_step_metrics source** — Read only from `metrics.per_step` in state.yaml. No on-the-fly computation from `step_history[]`. Rationale: single source of truth; the blocking branch owns that computation.

## Open Questions

1. **Blocking branch timing** — Should this ticket wait for `feature/metrics-capture-and-workflow-streamlining` to merge before starting implementation, or proceed with graceful-skip for absent `per_step` data and backfill post-merge? (AC-6 row counts will be low without it.)

2. **FK decision** — Recommendation is Approach B (no FKs). Does the architect override to Approach A or C for integrity enforcement? If yes, the rebuild ordering and upsert logic becomes more complex.

3. **`input_tokens`/`output_tokens`/`cost_usd` per-step columns** — idea.md lists these in `step_history` schema, but they don't exist in any current archive. Drop these columns now (simpler), or include them as NULL to future-proof when blocking branch data is available?

4. **`per_step_metrics` ingest source** — idea.md says ingest from `metrics.per_step` map in state.yaml. This key comes from `compute-swe-metrics.sh` on the blocking branch. The fallback source could also be computed on-the-fly from `step_history[]` during ingest (redundant but self-contained). Worth noting as an implementation choice.
