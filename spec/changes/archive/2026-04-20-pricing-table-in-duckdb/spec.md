---
feature-id: pricing-table-in-duckdb
linear-ticket: none
---

# Specification: Pricing table in DuckDB — Phase 1 of workflow-engine-as-state-machine

## Motivation

This feature is Phase 1 of the larger `workflow-engine-as-state-machine` effort, which moves every workflow artifact from filesystem YAML and inline scripts into a DuckDB-backed state machine. Pricing is the beachhead: it is the smallest self-contained domain that exercises the full migration-runner pattern (tracking table, ordered SQL files, idempotent re-application) end to end without touching the hot `step_events` write path. Landing the runner here makes it a reusable foundation for phases 2–5; landing it anywhere else would risk destabilising existing rows before the pattern is proven.

The secondary motivation is correctness. Today `config/pricing.yaml` is edited by hand, has no history, and is read by three disparate consumers (`record.py`, `cost_report.py`, `estimate-cost.sh`). A DuckDB `pricing` table with an `effective_from` timestamp gives the codebase a single source of truth and lays the groundwork for temporally-correct historical re-pricing in a future phase. Phase 1 does not exploit the temporal column (`gross_usd` in `cost_report.py` continues to use latest rates — see Decisions), but the schema is shaped from day one so the column is never retrofitted later.

## What Changes

- A new `schema_migrations(name TEXT PRIMARY KEY, applied_at TIMESTAMP)` tracking table and a lightweight, idempotent runner (`_run_migrations`) are added to `config/scripts/orchestrator_next/upsert.py`, alongside the existing `_migrate_*` helpers — one file, no new module.
- A new `pricing` table holds model pricing with columns `(model_id, input_usd, output_usd, cache_read_usd, cache_creation_usd, effective_from, is_local)`.
- A seed migration `0001_seed_pricing.sql` inserts every row currently in `config/pricing.yaml`, including the `__default__` sentinel, the date-aliased haiku row, the qwen row, and the local-model (`coder`) row, with `effective_from = '2025-01-01T00:00:00'`.
- `ensure_schema()` invokes the runner after the existing legacy `_migrate_*` calls. The two legacy Python ALTER helpers stay as-is — they are not retrofitted into the runner (retrofitting risks double-ALTER on existing DBs).
- `record.py` drops `@lru_cache`-backed YAML loading of pricing. `_compute_cost_usd` accepts the open DuckDB connection from its caller and resolves price via a parameterised SQL lookup.
- `cost_report.py` drops its `_load_pricing_for_model` YAML import and queries the `pricing` table directly. A `try/except duckdb.Error` fallback keeps `orchestrator cost` / `orchestrator metrics` working on DBs that predate the pricing migration (read-only opens skip `ensure_schema`).
- `jsonl_usage.py` is **not** modified. The `[1m]` variant strategy remains double-entry in the seed — if/when that variant actually appears in JSONL, add a row via the ingestion script. No hot-path logic changes.
- A new standalone `scripts/ingest-pricing.py` lets an operator insert a new pricing row with a new `effective_from` without touching the migration runner — recurring price updates are data, not schema.
- `estimate-cost.sh` is rewritten to query DuckDB via `duckdb -json -c "SELECT …"`, replacing the bash-AWK YAML parser. No new CLI subcommand.
- `config/pricing.yaml` is deleted in the final task, strictly after all three consumers have migrated and their tests are green.

## Requirements

### Functional

1. **FR-1**: `ensure_schema(db)` MUST create `schema_migrations` if absent, MUST discover every `*.sql` file under `config/scripts/orchestrator_next/migrations/` in lexical order, MUST skip files whose basename is already present in `schema_migrations`, and MUST execute and record every remaining file in a single transaction per file.
2. **FR-2**: The seed migration `0001_seed_pricing.sql` MUST create the `pricing` table DDL and insert every model currently in `config/pricing.yaml` — including the `__default__` sentinel row, the `claude-haiku-4-5-20251001` date-aliased row, and the `coder` local-model row (`is_local=true`) — all with `effective_from = '2025-01-01T00:00:00'`.
3. **FR-3**: `_compute_cost_usd(db, agent, usage)` MUST resolve price via a single parameterised SQL statement that returns the row with the greatest `effective_from <= now()` for the resolved `model_id`, falling back to the `__default__` row when no model match exists. The open DuckDB connection MUST be supplied by the caller; `_compute_cost_usd` MUST NOT open its own connection. Caller responsibility for the three call sites is: (a) `record.main()` opens a short-lived DB connection (resolving `METRICS_DB` then `$ORCHESTRATOR_HOME/metrics.duckdb`), calls `ensure_schema(db)`, threads `db` through `record()` into `_compute_cost_usd`, closes before return; when no path is resolvable (test/offline), `record.main()` passes `db=None` to `record()`, `_compute_cost_usd` returns `(model_id, None)`, `cost_usd` is left unset in the state.yaml step_history, a stderr warning is emitted, and no exception is raised. (b) `_ingest_driver_main` in `bin/orchestrator` relocates the `_compute_cost_usd` call to AFTER its `duckdb.connect` + `ensure_schema` (i.e., inside the `try:` block) and passes the already-open `db`. (c) `_ingest_subagents_main` in `bin/orchestrator` passes its already-open `db` (in scope at the call site) directly.
4. **FR-4**: `cost_report.py._load_pricing_for_model(db, model)` MUST query the `pricing` table directly (no YAML read) for its `gross_usd` dominant-model lookup AND MUST gracefully fall back to a built-in default-rate dict if the `pricing` table is absent on the read-only connection, so `orchestrator cost` / `orchestrator metrics` keep working on an un-migrated DB.
5. **FR-5**: `scripts/ingest-pricing.py` MUST accept a model-id, all four rate fields, an optional `--is-local`, and an `--effective-from` ISO timestamp (defaulting to UTC now), validate types, and INSERT a new row into `pricing` via a parameterised statement. The script MUST exit non-zero on any validation or DB error (including duplicate `(model_id, effective_from)`) and emit `inserted <model> @ <effective_from>` on success. `--help` MUST show at least one worked example.
6. **FR-6**: `estimate-cost.sh` MUST query pricing from DuckDB via `duckdb -json -readonly "$DB" "SELECT input_usd, output_usd, cache_read_usd FROM pricing WHERE model_id = '<model>' ORDER BY effective_from DESC LIMIT 1"` (or an equivalent single-call lookup) and parse the JSON output to produce the same preview-table output it produces today. The script MUST continue to work when the DB file is absent by falling back to conservative default rates (the current `default:` block in pricing.yaml).
7. **FR-7**: `config/pricing.yaml` MUST be deleted only after FR-3, FR-4, FR-5, and FR-6 are implemented and their tests are green.

### Non-Functional

1. **NFR-1**: `_compute_cost_usd` latency on a warmed-up connection MUST be at or below the current `@lru_cache`-backed YAML path (microsecond single-row lookup against an in-memory DuckDB is expected to match or beat it; a regression test compares 1000 iterations under a 50 ms budget).
2. **NFR-2**: The migration runner MUST be idempotent — invoking `ensure_schema` N times on the same DB (including from `orchestrator doctor` which opens RW and calls it) MUST NOT re-execute an applied migration and MUST NOT raise.
3. **NFR-3**: All new SQL MUST be parameterised (`db.execute(sql, params)`); no string interpolation of user-supplied data. This mirrors the existing `upsert.py` style.
4. **NFR-4**: Test coverage across modified `upsert.py` migration runner, modified `record.py` cost path, modified `cost_report.py` pricing path, and new `ingest-pricing.py` MUST be ≥ 90%.
5. **NFR-5**: **Multi-level metrics invariant.** `step_events.cost_usd` remains per-step. No phase-level, feature-level, or driver-level pricing concept is introduced. `pricing` is schema-agnostic and does not reference `step_events` columns. No new columns are added to `step_events`. This preserves future per-step / per-phase / per-feature / per-driver rollups as pure GROUP BY queries.
6. **NFR-6**: No new `orchestrator` CLI subcommands. Pricing tooling is standalone scripts (`scripts/ingest-pricing.py`) and direct `duckdb` CLI usage (`estimate-cost.sh`).

## Architecture

### File Modification Table

| File | Change | Notes |
|---|---|---|
| `config/scripts/orchestrator_next/upsert.py` | modify | Add `_DDL_SCHEMA_MIGRATIONS`, add `_run_migrations(db)` adjacent to existing `_migrate_*` helpers, call it from `ensure_schema` |
| `config/scripts/orchestrator_next/migrations/0001_seed_pricing.sql` | create | Pricing DDL + seed rows |
| `config/scripts/orchestrator_next/record.py` | modify | `_compute_cost_usd(db, agent, usage)`; drop `_load_pricing` lru_cache loader |
| `config/scripts/orchestrator_next/cost_report.py` | modify | `_load_pricing_for_model(db, model)` → SQL with `try/except duckdb.Error` fallback |
| `config/scripts/estimate-cost.sh` | modify | Replace `lookup_pricing` AWK block with `duckdb -json -readonly` lookup |
| `scripts/ingest-pricing.py` | create | Standalone ingestion script |
| `config/scripts/orchestrator_next/tests/test_migrations.py` | create | Runner tests |
| `config/scripts/orchestrator_next/tests/test_pricing_lookup.py` | create | SQL lookup tests |
| `config/scripts/orchestrator_next/tests/test_record_cost_compute.py` | modify | In-memory DuckDB fixture replaces monkeypatch + `_load_pricing.cache_clear()` |
| `config/scripts/orchestrator_next/tests/test_totals_wide.py` | modify | Same fixture change |
| `scripts/tests/test_ingest_pricing.py` | create | Ingestion script tests |
| `scripts/inline/tests/test_estimate_cost_sh.*` | create | Bash parity test |
| `config/pricing.yaml` | delete | Final task |

Downstream callers of `ensure_schema` (bin/orchestrator main, ingest-driver, ingest-subagents, mark-change-completed.sh, ingest-feature-metrics.py, doctor at `doctor.py:146`) are unchanged — they transparently pick up the new runner.

## Test Strategy

### Test File Paths

| Component | Test file |
|---|---|
| `_run_migrations` (in upsert.py) | `config/scripts/orchestrator_next/tests/test_migrations.py` (new) |
| `_compute_cost_usd` SQL path | `config/scripts/orchestrator_next/tests/test_record_cost_compute.py` (modified) and `tests/test_pricing_lookup.py` (new) |
| `_load_pricing_for_model` replacement | `config/scripts/orchestrator_next/tests/test_totals_wide.py` (modified) |
| `ingest-pricing.py` | `scripts/tests/test_ingest_pricing.py` (new) |
| `estimate-cost.sh` rewrite | `scripts/inline/tests/test_estimate_cost_sh.sh` (new) |

### Coverage Targets

≥ 90% on modified/new Python modules. Bash coverage not measured; rely on at least one end-to-end invocation with a seeded DuckDB fixture and one with the DB absent.

### Key Test Scenarios

- Migration runner: fresh DB → seed applies; second invocation → no-op; adding a dummy `0002_noop.sql` → only 0002 applies; broken SQL → raises and `schema_migrations` unchanged.
- Pricing lookup: exact model hit; date-aliased model hit (`claude-haiku-4-5-20251001`); unknown model → `__default__` fallback; multiple rows for one model → latest `effective_from <= ts` wins; `is_local=TRUE` row round-trips.
- `cost_report.py` read-only: DuckDB opened `read_only=True` against a DB with no `pricing` table → fallback dict, no raise.
- `estimate-cost.sh`: seeded DB returns expected rate; missing DB returns default rate; output format byte-identical for equivalent inputs (fixture parity test).
- `ingest-pricing.py`: happy path inserts row visible to subsequent `SELECT`; negative rate rejected; duplicate `(model_id, effective_from)` exits non-zero.

## Acceptance Criteria

- AC-1: Given an empty DuckDB file, when `ensure_schema(db)` is called, then `schema_migrations` and `pricing` tables exist, `schema_migrations` contains `('0001_seed_pricing.sql', …)`, and `SELECT COUNT(*) FROM pricing` equals the YAML model count plus `__default__`. [traces: UC-1]
- AC-2: Given a DuckDB file that already has `schema_migrations` with `0001_seed_pricing.sql` recorded, when `ensure_schema(db)` is called a second time, then no duplicate rows are inserted and no exceptions are raised. [traces: UC-2, UC-E2]
- AC-3: Given a step record with `usage.model = "claude-sonnet-4-6"` and 22000 input / 5000 output tokens, when `record.main()` runs with a seeded DB, then `step_events.cost_usd` equals `22000*3.0/1e6 + 5000*15.0/1e6 = 0.141000` (same value the YAML loader produced). [traces: UC-3]
- AC-4: Given a step record with `usage.model = "unknown-model-xyz"`, when `record.main()` runs, then cost is computed against the `__default__` row (opus-tier rates) and a warning is written to stderr. [traces: UC-E1]
- AC-5: Given a DuckDB DB with `pricing` seeded, when `estimate-cost.sh <state-dir>` runs, then it emits the same `route_preview:` YAML block (for the same archive inputs) that it emitted before the rewrite — verified by byte diff against a captured fixture. [traces: UC-4]
- AC-6: Given a DuckDB DB WITHOUT the pricing migration applied, when `orchestrator cost` or `orchestrator metrics` opens it `read_only=True`, then the command completes with a best-effort report using default rates and does NOT raise. [traces: UC-E3]
- AC-7: Given `scripts/ingest-pricing.py --model claude-haiku-5-0 --input-usd 1.0 --output-usd 5.0 --cache-read-usd 0.1 --cache-creation-usd 1.25 --effective-from 2026-06-01T00:00:00`, when the script is run, then a row with exactly those values exists in `pricing` and the script prints `inserted claude-haiku-5-0 @ 2026-06-01T00:00:00` and exits 0. Running the same command twice exits non-zero on the second invocation. [traces: UC-1, UC-2]
- AC-8: Given all AC-1 through AC-7 pass, when `config/pricing.yaml` is deleted and the full test suite is re-run, then every test passes and `git grep pricing.yaml` returns no Python/bash references (comments and docs-of-removal may remain). [traces: UC-1, UC-3, UC-4]
- AC-9: Given any task in this feature, when the design is reviewed, then `step_events` has the same schema as before (no new columns, no new indexes, no phase/feature/driver pricing column) and `pricing` contains no `step_events`-specific fields. [traces: UC-3] — enforces NFR-5.

## Alternatives Considered

**Alternative 1: yoyo-migrations off-the-shelf library**
Rejected. Adds a runtime dependency for ~40 lines of custom runner code, does not solve the bash-consumer problem, and yoyo's DuckDB backend is community-maintained. The existing `upsert.py` `_migrate_*` functions already demonstrate the team builds these inline.

**Alternative 2: Re-seed pricing from YAML on every startup (no migration runner)**
Rejected. Defeats the phase-1 goal ("DuckDB is the single source of truth"), renders `effective_from` meaningless, and leaves phases 2–5 without the runner they require. Produces debt, not a foundation.

**Alternative 3: Add `orchestrator pricing lookup <model>` CLI subcommand for estimate-cost.sh**
Rejected. The parent workflow-engine-as-state-machine effort is locked to exactly two verbs (`next`, `done`) at its end state. Adding a subcommand now is strictly backwards motion. Direct `duckdb -json -c` is the simpler path and survives the parent refactor.

**Alternative 4: Retain `config/pricing.yaml` as a derived artifact written by the runner**
Rejected. Keeps two sources of truth in lock-step forever, complicates every future pricing change, and undermines the phase-1 premise.

**Alternative 5: Put the migration runner in a new `orchestrator_next/migrations.py` module**
Rejected. `_migrate_step_events` and `_migrate_tool_calls` already live in `upsert.py`. Adding `_run_migrations` as a sibling keeps all schema-evolution logic in one file and matches the codebase's existing pattern. One file, one import, less surface area.

**Alternative 6: Normalize model strings (strip `[1m]`/date suffixes) at the `jsonl_usage.py` boundary**
Rejected. The seed migration already mirrors the YAML's double-entry strategy (explicit rows for date-aliased variants). If a `[1m]` variant ever appears in real JSONL, the ingestion script can add a row. Adding string-normalization logic to a hot ingestion path (and to `_compute_cost_usd`) changes behavior beyond the storage refactor and is out of scope.

## Impact

- **Breaking API changes (internal)**: `_compute_cost_usd` signature changes from `(agent, usage)` → `(db, agent, usage)`. THREE call sites update atomically: `record.py` line 394 (inside `record()`, db threaded from `record.main()` which opens it), `bin/orchestrator:337` (`_ingest_driver_main`, relocate after db open), `bin/orchestrator:473` (`_ingest_subagents_main`, db already in scope). `_load_pricing_for_model` signature changes from `(model)` → `(db, model)`; its callers in `cost_report.py` are updated in the same task. A stale `from orchestrator_next.record import _load_pricing` import in `cost_report.py` is scrubbed at the same time (T-8) to avoid a silent `ImportError` swallowed by the fallback try/except after `_load_pricing` is deleted in T-6.
- **No user-facing CLI changes.** `orchestrator` verbs are unchanged; `estimate-cost.sh` stdout/stderr stay byte-identical.
- **Migration on upgrade**: first `orchestrator next` / `record` / `doctor` after pull auto-runs the seed migration; no manual step.
- **Affected areas**: `record.py` cost path, `cost_report.py` gross_usd path, bash `estimate-cost.sh`, test fixtures.
- **Rollback**: revert commits; the `pricing` and `schema_migrations` tables being present does not affect the pre-revert YAML-based code paths.

## Decisions

- **Approach A chosen** (custom runner + `schema_migrations` + standalone ingestion script; bash consumer rewrites to `duckdb -json`): simplest build, zero new dependencies, matches existing `upsert.py` pattern, honours the two-verb end-state constraint.
- **`gross_usd` keeps latest-rates semantics** (OQ-2): changing the historical calculation affects external dashboards and is out of scope for phase 1; `cost_report.py` carries a one-line `TODO(phase-N): temporal-correctness follow-up` at the lookup site.
- **`[1m]` and date-suffix variants: preserve YAML double-entry in the DB seed** (OQ-3): no stripping logic added to `jsonl_usage.py` or `_compute_cost_usd`. If/when new variants appear, add a pricing row via the ingestion script. Zero hot-path logic change.
- **`TIMESTAMP` (not `TIMESTAMPTZ`)** (OQ-4): matches the existing `step_events.started_at` / `ended_at` convention; DDL comment documents "stored UTC by convention."
- **Runner lives inside `upsert.py`** (OQ-5): follows the existing `_migrate_step_events` / `_migrate_tool_calls` pattern. No new module.
- **`orchestrator doctor` RW path is safe** (OQ-6): verified at `config/scripts/orchestrator_next/doctor.py:146` — already opens RW and calls `ensure_schema`; the runner's idempotence covers this call site. No change needed.
- **Legacy `_migrate_step_events` / `_migrate_tool_calls` stay as inline Python ALTERs** and are NOT retrofitted into `schema_migrations` — retrofitting risks double-ALTER on existing DBs. New table/column evolution from phase 2 onward uses SQL migration files.
