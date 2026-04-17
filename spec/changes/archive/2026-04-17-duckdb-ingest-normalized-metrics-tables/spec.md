---
feature-id: duckdb-ingest-normalized-metrics-tables
linear-ticket: HL-286
---

# Spec — duckdb-ingest-normalized-metrics-tables (HL-284)

## Motivation

Today every cost/quality/hotspot query on `metrics.duckdb` pays a `json_extract(payload_json, ...)` tax to reach per-step and per-agent data. This blocks `/learn`, `/telemetry`, and future dashboards from expressing natural SQL like "which step costs the most across all refactor features?" without verbose JSON path expressions and casts.

Normalize per-step and per-agent usage into typed columns in three new DuckDB tables — `step_history`, `per_agent_metrics`, `per_step_metrics` — populated at ingest time inside `register-repo.sh`. Extend `metrics-query.sh` with three named queries that exercise the new tables. Keep `payload_json` for now (out-of-scope to drop).

## Requirements

### Functional

- **FR-1**: `register-repo.sh` MUST create three new tables (`step_history`, `per_agent_metrics`, `per_step_metrics`) via `CREATE TABLE IF NOT EXISTS` alongside the existing `features` DDL. Repeated runs MUST NOT error or duplicate schema. [traces: UC-1, AC-1]
- **FR-2**: On each ingest of a `state.yaml`, the script MUST populate `step_history` with one row per entry in the `step_history[]` YAML array, indexed by a zero-based `step_ord`. [traces: UC-1, AC-2]
- **FR-3**: On each ingest, the script MUST populate `per_agent_metrics` with one row per key in `metrics.per_agent_tokens` when that object exists; absent → skip silently (no error, no partial row). [traces: UC-1, UC-E2, AC-2]
- **FR-4**: On each ingest, the script MUST populate `per_step_metrics` with one row per entry in `metrics.per_step` when that map exists; absent → skip silently. [traces: UC-1, UC-E1, AC-2]
- **FR-5**: Re-ingest of the same `(repo_root, change_id)` MUST produce identical row counts in all four tables (features + 3 new). The script MUST delete child rows for `(repo_root, change_id)` before `INSERT OR REPLACE INTO features`, then re-insert child rows. [traces: UC-2, AC-2]
- **FR-6**: `--rebuild` MUST delete all rows in the three new tables for the target `repo_root` before deleting `features` rows, then re-ingest all archives. [traces: UC-2, AC-2]
- **FR-7**: `metrics-query.sh` MUST support three new query ids:
  - `step-cost-hotspots`: `SELECT step_id, SUM(cost_usd) ... FROM per_step_metrics WHERE <scope> GROUP BY step_id ORDER BY 2 DESC`
  - `agent-cost-hotspots`: `SELECT agent, SUM(total_tokens), SUM(cost_usd) ... FROM per_agent_metrics WHERE <scope> GROUP BY agent ORDER BY SUM(total_tokens) DESC`
  - `agent-duration-outliers`: agents with `AVG(duration_ms) > 2x overall AVG(duration_ms)` within scope [traces: UC-3, UC-4, AC-4]
- **FR-8**: All three new queries MUST honour `--repo`, `--fleet`, and `--limit` flags using the existing scope-clause pattern. [traces: UC-3, UC-4, AC-7]
- **FR-9**: New queries MUST NOT use `json_extract`. A consumer query `SELECT agent, total_tokens FROM per_agent_metrics WHERE change_id = 'X'` MUST return one row per agent without JSON functions. [traces: AC-3]
- **FR-10**: Ingest MUST be non-blocking on missing `metrics.per_step`, missing `metrics.per_agent_tokens`, and step entries with no `usage` block (NULL the numeric columns). No error output, no exit != 0. [traces: UC-E1, UC-E2, UC-E3]
- **FR-11**: A documented backfill run (`register-repo.sh --rebuild` against orchestrator repo) MUST be executed; resulting row counts for the three new tables MUST be recorded in `verify.md` or task `Verify:` output. [traces: AC-6]

### Non-Functional

- **NFR-1**: New DDL MUST NOT declare `FOREIGN KEY` constraints. Rationale: DuckDB v1.5.2 lacks `ON DELETE CASCADE` and enforces FK during `INSERT OR REPLACE INTO features` (implemented as DELETE+INSERT), which would break the existing upsert pattern. Consistency is enforced at the application layer (child-first delete ordering). Behaviour on manual `DELETE FROM features WHERE ...` outside the script: orphan rows in child tables are possible — documented in `design.md` and this spec. [traces: AC-8]
- **NFR-2**: All existing 27+ assertions in `metrics-query.test.sh` MUST continue to pass. [traces: AC-5]
- **NFR-3**: `step_history.step_ord` MUST be a stable, zero-based index reflecting YAML array order so that `ORDER BY step_ord` reproduces archive order.
- **NFR-4**: `sql_quote` must be applied to every interpolated string to preserve the existing SQL-injection defence.
- **NFR-5**: Test fixtures MUST isolate from the production DB via `METRICS_DB` env override, matching the existing pattern.

### Acceptance Criteria

- **AC-1** [traces: UC-1]: `register-repo.sh` executes twice in succession without error; `SELECT name FROM (SHOW TABLES)` returns `features, step_history, per_agent_metrics, per_step_metrics`.
- **AC-2** [traces: UC-1, UC-2]: After ingesting a fixture state.yaml containing `step_history[]`, `per_agent_tokens`, and `per_step`, row counts match expected (e.g., N steps → N rows in `step_history`, M agents → M rows in `per_agent_metrics`, K step_ids in per_step → K rows in `per_step_metrics`). Re-running ingest yields the same counts.
- **AC-3** [traces: UC-3]: `duckdb metrics.duckdb "SELECT agent, total_tokens FROM per_agent_metrics WHERE change_id='<fixture>'"` returns one row per agent — no `json_extract` anywhere in the query.
- **AC-4** [traces: UC-3, UC-4]: `metrics-query.sh step-cost-hotspots --repo <r>`, `agent-cost-hotspots --repo <r>`, `agent-duration-outliers --repo <r>` each exit 0 with non-empty output against a seeded fixture that has per-step and per-agent data.
- **AC-5** [traces: existing coverage]: `bash config/scripts/metrics-query.test.sh` reports `Results: N passed, 0 failed` with N ≥ 27 + new-assertion count.
- **AC-6** [traces: UC-1, UC-2]: `register-repo.sh --rebuild` executed against the orchestrator repo; `verify.md` records actual row counts for `step_history`, `per_agent_metrics`, `per_step_metrics`. (Expected: `step_history` > 0, `per_agent_metrics` small non-zero, `per_step_metrics` == 0 until the blocking branch merges — this is acknowledged expected behaviour, not a defect.)
- **AC-7** [traces: UC-3]: `metrics-query.sh --fleet step-cost-hotspots` returns aggregation across all repos registered in `metrics-registry.yaml`.
- **AC-8** [traces: NFR-1]: `DELETE FROM features WHERE repo_root='...' AND change_id='...'` executed directly (outside `register-repo.sh`) does NOT error and does NOT cascade; orphan rows remain in child tables. `design.md` documents this explicitly and states the operator contract: "always use `register-repo.sh --rebuild` for deletion."

## Alternatives Considered

**A1: Keep JSON-only, add indexed computed views.** Rejected — DuckDB materialized views still pay the `json_extract` cost on refresh and don't solve the query ergonomics problem for dashboards that connect directly.

**A2: FK-enforced schema (Approach A from discovery).** Rejected — DuckDB FKs break `INSERT OR REPLACE` and lack `ON DELETE CASCADE`. The integrity benefit is illusory for a single-writer bash script; the cost is a materially more complex rebuild/upsert flow.

**A3: Wait for blocking branch `feature/metrics-capture-and-workflow-streamlining` to merge.** Rejected — graceful-skip on missing `per_step` and `per_agent_tokens` is trivial and lets this work land independently. Operator reruns `--rebuild` post-merge to populate per-step rows.

**A4: Compute `per_step_metrics` on-the-fly from `step_history[]` during ingest.** Rejected — duplicates the logic owned by `compute-swe-metrics.sh` on the blocking branch. Single source of truth is simpler.

## Scope Guardrails

Out of scope (explicit): dropping `payload_json`, adding speculative per-step cost columns (`input_tokens`, `output_tokens`), FK cascade semantics, any changes to agent skills / step contracts / state.yaml format.
