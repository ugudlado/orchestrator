---
feature-id: pricing-table-in-duckdb
linear-ticket: null
---

# Discovery Brief: Pricing table in DuckDB — Phase 1 of workflow-engine-as-state-machine

## What I Understand

The underlying goal is not merely "move YAML to a database." It is Phase 1 of a 5-phase refactor that makes DuckDB the single source of truth for all workflow state and operational data. The pricing table is the beachhead: it is the simplest domain that exercises the full migration runner pattern (seed → apply → verify) without touching existing step_events rows, making it safe to land first. Once pricing lives in DuckDB, every subsequent phase can assume the migration runner exists and the pattern is proven.

The secondary driver is correctness: `config/pricing.yaml` is edited manually and has no history. A DuckDB table with `effective_from` timestamps allows future phases to re-price historical runs at the correct rate. Phase 1 does not yet exploit this (cost_report.py gross_usd continues to use latest rates), but the schema must be correct from day one so the column is not retrofitted later.

## Feature Summary

Move Claude model pricing data from `config/pricing.yaml` into a DuckDB `pricing` table managed by the existing `ensure_schema()` entry point. A lightweight migration runner (backed by a `schema_migrations` tracking table) applies ordered SQL migrations; the seed migration inserts all current pricing rows with `effective_from` UTC timestamps. `record.py` replaces its `@lru_cache`-backed YAML loader with a SQL lookup. After the migration runner and all Python consumers are switched, `config/pricing.yaml` is deleted. The bash consumer (`estimate-cost.sh`) must be addressed before deletion is safe.

## Personas & Actors

- **Workflow driver** (bin/orchestrator): executes `orchestrator record` to persist step events; `_compute_cost_usd` is the hot path that reads pricing on every step completion.
- **Cost reporter** (orchestrator cost / orchestrator metrics): reads step_events and pricing for display; opens DB read-only and does not call `ensure_schema`.
- **Spec-phase estimator** (scripts/inline/preview-route.sh → estimate-cost.sh): bash script run during the specify phase to estimate feature cost; currently reads pricing.yaml directly via AWK.
- **Test suite** (pytest): monkeypatches `ORCHESTRATOR_HOME` and clears `_load_pricing` lru_cache; fixture strategy must adapt to DuckDB lookup.
- **Future workflow phases** (phases 2–5 of workflow-engine-as-state-machine): will build on the migration runner established here.

## Use Cases

### Happy Path

UC-1: Fresh DB initialization — the workflow driver runs `orchestrator next` for the first time; `ensure_schema()` creates `schema_migrations` and `pricing` tables, applies the seed migration, and inserts all model rows. Subsequent calls to `_compute_cost_usd` resolve pricing via SQL within the same connection.

UC-2: Incremental migration on upgrade — an existing DB already has `step_events` and `pricing`. A new migration file is added to `config/scripts/orchestrator_next/migrations/`. On next `ensure_schema()` call, only the new migration is applied; previously applied migrations are skipped because their names exist in `schema_migrations`.

UC-3: Cost compute on step completion — `orchestrator record` is called with a step payload; `_compute_cost_usd` queries `SELECT input_usd, output_usd, cache_read_usd, cache_creation_usd FROM pricing WHERE model_id = ? AND effective_from <= ? ORDER BY effective_from DESC LIMIT 1` and falls back to `default` row if no model match. Cost is written to `step_events.cost_usd`.

UC-4: Spec-phase estimate — `preview-route.sh` calls `estimate-cost.sh`; the script reads pricing from DuckDB (or pricing.yaml is retained as a derived artifact for this phase) and computes the pre-implementation cost estimate. No change to the estimate output format.

### Error & Edge Cases

UC-E1: Unknown model string — `_compute_cost_usd` receives a model_id not in the `pricing` table (e.g., a new model, or a model with an unstripped `[1m]` suffix). The SQL query returns zero rows; the code falls back to the `default` row (model_id = `__default__`). Cost is logged with a warning; the step record is not dropped.

UC-E2: Migration applied twice — the migration runner is invoked when `schema_migrations` already contains the seed migration name. The runner queries `schema_migrations` first; the migration is skipped. No error, no duplicate rows.

UC-E3: DB opened read-only before pricing table exists — `orchestrator cost` or `orchestrator metrics` opens the DB with `read_only=True` before any `ensure_schema` call has seeded the pricing table. The read-only opener does not call `ensure_schema`; the pricing table query fails. This is a pre-existing risk, not introduced by this feature, but the design must not worsen it (cost_report.py only reads `cost_usd` from step_events; it does not query the pricing table directly, so this risk does not apply to cost_report).

## Scope

### In Scope

- `schema_migrations` table: DDL in `ensure_schema()`, runner function that applies SQL files from `config/scripts/orchestrator_next/migrations/` in lexical order, idempotent.
- `pricing` table: DDL with columns `(model_id TEXT PRIMARY KEY, input_usd DOUBLE, output_usd DOUBLE, cache_read_usd DOUBLE, cache_creation_usd DOUBLE, effective_from TIMESTAMP, is_local BOOLEAN)` plus a sentinel `__default__` row.
- Seed migration: `0001_seed_pricing.sql` — INSERT OR REPLACE for all rows currently in `config/pricing.yaml`, with `effective_from = '2025-01-01T00:00:00'` (conservative backdate).
- `record.py`: replace `_load_pricing()` lru_cache + YAML loader with a SQL lookup function; update `_compute_cost_usd` to use it; update test fixtures.
- `cost_report.py`: replace `_load_pricing_for_model()` import with equivalent DuckDB query.
- `config/pricing.yaml`: deleted after Python consumers are migrated and `estimate-cost.sh` is resolved.
- `estimate-cost.sh` disposition: explicit decision before deletion (see Scope Constraint below).
- Tests: update `test_record_cost_compute.py` and `test_totals_wide.py` to use an in-memory DuckDB fixture instead of monkeypatching `ORCHESTRATOR_HOME`.

### Out of Scope

- Re-pricing historical `step_events` rows — cost_usd is frozen at write time; this feature does not retroactively correct past costs.
- UI or reporting changes — gross_usd in cost_report.py continues to use latest pricing rates (semantic question deferred to architect).
- `orchestrator cost` / `orchestrator metrics` read-only DB path — these open with `read_only=True` and bypass `ensure_schema`; no change to that behavior in phase 1.
- Model name normalization (stripping date suffixes or `[1m]` suffix) — deferred; the existing double-entry strategy in pricing.yaml is preserved in the DB seed.
- Phases 2–5 of workflow-engine-as-state-machine — this feature ends with pricing in DuckDB and pricing.yaml deleted.
- TIMESTAMPTZ — existing schema uses TIMESTAMP; this feature does not change the column type convention (deferred to architect if UTC correctness is a requirement).

## What Already Exists

### Codebase

**pricing.yaml consumers (3 call sites):**

1. `config/scripts/orchestrator_next/record.py:52` — `@functools.lru_cache(maxsize=1)` `_load_pricing()` reads `config/pricing.yaml`. Called by `_compute_cost_usd` on every step record.
2. `config/scripts/orchestrator_next/cost_report.py:62` — `_load_pricing_for_model(model)` imports `_load_pricing` from record.py. Used only for `gross_usd` display metric; does NOT re-price historical step_events rows.
3. `config/scripts/estimate-cost.sh:41,111–135` — bash AWK `lookup_pricing()` reads `$ORCHESTRATOR_HOME/config/pricing.yaml` directly. **This is a third consumer that is NOT a Python module.** Called by `scripts/inline/preview-route.sh` during the specify phase. Cannot be dropped when pricing.yaml is deleted unless the script is rewritten or pricing.yaml is retained as a derived artifact.

**`ensure_schema()` callers (5 locations):**

1. `bin/orchestrator` main dispatch path — called before `orchestrator next` and `orchestrator record`
2. `bin/orchestrator` `_ingest_driver_main()` — called before driver JSONL ingest
3. `bin/orchestrator` `_ingest_subagents_main()` — called before subagent JSONL ingest
4. `scripts/inline/mark-change-completed.sh` — line 56–60, opens DB and calls `ensure_schema`
5. `scripts/inline/ingest-feature-metrics.py:413` — calls `ensure_schema(db)`

**Read-only openers (do NOT call ensure_schema):**
- `bin/orchestrator` `_cost_main()` line 229: `duckdb.connect(db_path, read_only=True)`
- `bin/orchestrator` `_metrics_main()` line 114: `duckdb.connect(db_path, read_only=True)`

**Existing migration pattern in `upsert.py`:**
- `_migrate_step_events(db)` at line 239: DESCRIBE TABLE → check columns → ALTER TABLE if missing — idempotent, no tracking table
- `_migrate_tool_calls(db)` at line 275: same pattern for `duration_ms`
- No `schema_migrations` table exists today — migrations are hardcoded Python functions, not ordered SQL files

**Test fixtures (must change):**
- `tests/test_record_cost_compute.py:109–114`: `monkeypatch.setenv("ORCHESTRATOR_HOME", ...)` + `_load_pricing.cache_clear()` — fixture strategy breaks when `_load_pricing` is replaced by a SQL call
- `tests/test_totals_wide.py:33–38`: hardcodes Sonnet-4-5 rates from pricing.yaml; `test_totals_includes_pricing_subdict` calls `_load_pricing_for_model` indirectly

**Model name canonical forms (from test evidence):**
- `tests/test_record_cost_compute.py` asserts `model == "claude-sonnet-4-6"` and `model == "claude-opus-4-7"` — no date suffix, no `[1m]` suffix
- `config/scripts/orchestrator_next/jsonl_usage.py:82–84`: model string is raw passthrough from JSONL; no stripping performed at ingest

**Migrations directory:** `config/scripts/orchestrator_next/migrations/` — does NOT exist yet.

### External

**Alembic** (SQLAlchemy-based): designed for relational DBs with dialect-aware DDL. DuckDB has partial SQLAlchemy support; Alembic adds significant dependency weight and is designed for client-server DBs, not single-process embedded. Not a fit.

**yoyo-migrations**: lightweight SQL file runner with a `_yoyo_migration` tracking table. Python-native. Would work, but adds an external dependency for ~40 lines of custom runner code. The existing upsert.py migration pattern demonstrates the team already builds these inline.

**dbmate**: CLI tool that runs SQL files. Requires a separate binary install and is designed for multi-user server DBs, not embedded DuckDB in a Python process. Not a fit.

**DuckDB built-in**: DuckDB has no built-in schema versioning. Migration management is userspace.

## Build-or-Reuse Decision

**Build.** The custom migration runner is the right choice:

1. The codebase already has the idempotent migration pattern (upsert.py `_migrate_*` functions) — the runner formalizes it with a tracking table.
2. Off-the-shelf tools (alembic, yoyo) add dependency weight for ~40 lines of code. This repo's tech_stack is bash/python/yaml/duckdb — adding a migrations library is scope creep.
3. The single-process embedded DuckDB model has no concurrent migration risk — the runner does not need distributed locking or rollback.
4. The estimate-cost.sh bash consumer is an exception that cannot be solved by any migration library — it requires a separate disposition decision regardless.

## Approaches Considered

### Approach A (recommended): Custom migration runner + `schema_migrations` table

Core idea: Add a `_run_migrations(db)` function to `upsert.py` (or a new `migrations.py`). It creates `schema_migrations(name TEXT PRIMARY KEY, applied_at TIMESTAMP)` if it does not exist, then reads `config/scripts/orchestrator_next/migrations/*.sql` in lexical order, skips any name already in `schema_migrations`, executes the rest, and records each applied migration. `ensure_schema()` calls `_run_migrations(db)` after existing DDL.

Seed migration `0001_seed_pricing.sql`: creates `pricing` DDL and inserts all rows from current pricing.yaml with `effective_from = '2025-01-01T00:00:00'`.

record.py: replaces `_load_pricing()` + lru_cache with a `_get_pricing_from_db(db, model_id, ts)` SQL function. `_compute_cost_usd` receives the db connection (or opens one).

estimate-cost.sh: pricing.yaml is retained as a derived artifact (generated from the DB) or the script is rewritten to call `orchestrator pricing lookup <model>` — decision required before pricing.yaml is deleted.

Pros: no new dependencies, follows existing patterns, SQL files are auditable diffs, tracking table enables future migration analytics.
Cons: custom code to maintain; no rollback support (acceptable for forward-only DDL).
Effort: medium (runner ~40 lines, seed migration, record.py refactor, test fixture updates).

### Approach B: Off-the-shelf migration library (yoyo-migrations)

Core idea: Add `yoyo` as a dependency, configure it with the DuckDB path and `migrations/` directory. `ensure_schema()` calls yoyo's `read_migrations()` + `backend.apply_migrations()`.

Pros: battle-tested tracking table, rollback support, CLI tooling for manual migration runs.
Cons: adds an external dependency; yoyo's DuckDB backend support is community-maintained and may lag DuckDB versions; does not solve the estimate-cost.sh bash consumer; rollback support is unused (we do forward-only DDL). Overkill for this codebase.
Effort: medium (same as A, plus dependency management and yoyo config).

### Approach C: Re-seed from YAML on every startup (no migration runner)

Core idea: Drop the migration runner entirely. `ensure_schema()` always runs `INSERT OR REPLACE` from a Python dict (loaded from pricing.yaml at startup). The YAML file is the source of truth; DuckDB is a cache.

Pros: simplest possible implementation — no tracking table, no SQL files.
Cons: defeats the stated goal ("DuckDB is the single source of truth"). The `effective_from` column has no value if pricing is always overwritten. The migration runner is required for phases 2–5 anyway. This approach produces technical debt, not a foundation.
Effort: small (but wrong).

## Recommendation

**Approach A.** It is the simplest build that achieves the stated goal. Approach B adds dependency overhead for no functional gain at this scale. Approach C is architecturally wrong relative to the phase 1 intent.

Key constraint that must be resolved before `config/pricing.yaml` is deleted: the `estimate-cost.sh` bash AWK consumer. The architect must decide: (a) rewrite the script to query DuckDB directly (Python subprocess or duckdb CLI), (b) add an `orchestrator pricing lookup <model>` subcommand, or (c) retain `config/pricing.yaml` as a generated artifact written by the migration runner. Option (c) is the lowest-risk path for phase 1 — pricing.yaml becomes read-only output, not the source of truth.

## UI Direction

N/A — no UI components.

## Constraints / CLI Surface Inventory

All callable entrypoints in the CLI and script surface that touch pricing or ensure_schema:

| Entrypoint | Path | Pricing touch | ensure_schema call |
|---|---|---|---|
| `orchestrator next` | bin/orchestrator | no | yes (main path) |
| `orchestrator record` | bin/orchestrator | yes (_compute_cost_usd) | yes (main path) |
| `orchestrator cost` | bin/orchestrator | no (reads cost_usd from step_events) | no (read_only=True) |
| `orchestrator metrics` | bin/orchestrator | no | no (read_only=True) |
| `orchestrator ingest-driver` | bin/orchestrator | yes (_compute_cost_usd for driver-loop) | yes |
| `orchestrator ingest-subagents` | bin/orchestrator | no | yes |
| `orchestrator doctor` | bin/orchestrator | no | unclear — verify |
| `mark-change-completed.sh` | scripts/inline/ | no | yes |
| `ingest-feature-metrics.py` | scripts/inline/ | no | yes |
| `estimate-cost.sh` | config/scripts/ | YES (bash AWK reads pricing.yaml) | no |
| `preview-route.sh` | scripts/inline/ | indirect (calls estimate-cost.sh) | no |

## Technical Context

- **Files to create**: `config/scripts/orchestrator_next/migrations/0001_seed_pricing.sql`, migration runner function (in `upsert.py` or new `migrations.py`)
- **Files to modify**: `config/scripts/orchestrator_next/upsert.py` (ensure_schema + runner), `config/scripts/orchestrator_next/record.py` (_load_pricing → SQL), `config/scripts/orchestrator_next/cost_report.py` (_load_pricing_for_model → SQL), `config/scripts/orchestrator_next/tests/test_record_cost_compute.py` (fixture), `config/scripts/orchestrator_next/tests/test_totals_wide.py` (fixture), `bin/orchestrator` (if orchestrator pricing subcommand added for estimate-cost.sh)
- **Files to delete**: `config/pricing.yaml` (after all consumers migrated)
- **Library versions**: DuckDB (version in use — verify with `python -c "import duckdb; print(duckdb.__version__)"`)
- **DuckDB TIMESTAMP convention**: existing schema uses `TIMESTAMP` (not `TIMESTAMPTZ`); this feature follows the same convention
- **lru_cache invalidation**: `_load_pricing.cache_clear()` is called in test teardown; the replacement SQL function must have an equivalent test-time reset mechanism (or use a passed-in connection that test fixtures control)
- **Effective_from semantics**: `greatest effective_from <= ts` lookup is correct for temporal pricing; the seed migration must use a date before any real step_events rows (2025-01-01 is safe given the project start date)

## Open Questions

- OQ-1: **estimate-cost.sh disposition** — Which option does the architect choose: (a) rewrite bash to query DuckDB, (b) add `orchestrator pricing lookup` CLI subcommand, or (c) keep pricing.yaml as a generated artifact? Option (c) is lowest-risk for phase 1 but defers true "single source of truth" to a follow-up.
- OQ-2: **gross_usd semantic** — cost_report.py uses "latest pricing rates" for gross_usd (not ts-scoped historical rates). When pricing changes, historical gross_usd becomes inaccurate. Is this acceptable, or should gross_usd use `effective_from <= step_completed_at` lookup? Architect decision.
- OQ-3: **`[1m]` suffix in JSONL** — jsonl_usage.py passes model strings through without normalization. If Claude Code emits `claude-opus-4-7[1m]` in session JSONL, that string reaches `_compute_cost_usd` and would fall back to default pricing. Is the double-entry strategy (pricing.yaml today) extended to cover `[1m]` variants, or is stripping added to jsonl_usage.py?
- OQ-4: **TIMESTAMPTZ vs TIMESTAMP** — existing schema uses TIMESTAMP; UTC correctness is not guaranteed if the system clock is not UTC. Should `effective_from` use TIMESTAMPTZ for explicitness? Architect decision.
- OQ-5: **Migration runner location** — new function in `upsert.py` (keeps schema logic together) or new `migrations.py` module (separation of concerns)? Architect preference.
- OQ-6: **`orchestrator doctor`** — does `_doctor_main()` call `ensure_schema`? Verify before implementation to ensure the doctor subcommand does not execute migrations unexpectedly.

## Key Decisions (architect, 2026-04-20)

Resolved during design pass. All six open questions closed; no re-exploration required.

- **Approach A selected** (custom migration runner in `upsert.py` + `schema_migrations` table + standalone ingestion script; bash consumer shells out to `duckdb -json` CLI). Approaches B and C ruled out in discovery; not re-evaluated.
- **OQ-1 — estimate-cost.sh disposition:** rewrite bash to query DuckDB directly via `duckdb -readonly -json -c "SELECT …"`. No new CLI subcommand; `config/pricing.yaml` is deleted (not retained as derived artifact). Aligns with the parent effort's two-verb end-state (`next` / `done` only).
- **OQ-2 — gross_usd temporal semantics:** keep latest-rates semantics in `cost_report.py`. A one-line `TODO(phase-N): temporal-correctness follow-up` annotates the lookup site. Changing historical dashboard numbers is out of scope for phase 1.
- **OQ-3 — `[1m]` / date-suffix model variants:** preserve the YAML's double-entry strategy in the DB seed. No normalization logic added to `jsonl_usage.py` or `_compute_cost_usd`. New variants are handled by appending a row via `scripts/ingest-pricing.py` — a data change, not a code change. Zero hot-path logic change.
- **OQ-4 — TIMESTAMP vs TIMESTAMPTZ:** `TIMESTAMP`. Matches existing `step_events.started_at` / `ended_at` convention. DDL comment documents "values stored UTC by convention."
- **OQ-5 — Migration runner location:** `upsert.py`, as a sibling to `_migrate_step_events` / `_migrate_tool_calls`. No new `migrations.py` module. One file, one import, less surface area — follows the team's existing pattern.
- **OQ-6 — `orchestrator doctor` ensure_schema call:** verified at `config/scripts/orchestrator_next/doctor.py:146` — already opens RW and calls `ensure_schema`. The runner is idempotent by design (applied names tracked in `schema_migrations`), so no special-casing needed. No action required.
- **Recurring price updates** handled by `scripts/ingest-pricing.py`, NOT by new migration files. `schema_migrations` is DDL history; `pricing` rows are rate history. Clean separation.
- **Legacy inline Python ALTERs (`_migrate_step_events`, `_migrate_tool_calls`) stay outside the runner.** Retrofitting risks double-ALTER on existing DBs. Phase 2+ schema evolution goes through SQL migration files from the start.
- **Multi-level metrics invariant preserved:** `step_events.cost_usd` remains per-step; no new columns in `step_events`; `pricing` is schema-agnostic (no phase / feature / driver concepts). Per-level rollups in later phases remain pure GROUP BY queries.
- **DB-acquisition for `_compute_cost_usd` (post-review refinement, 2026-04-20):** `record.main()` opens a short-lived DuckDB connection (resolving `METRICS_DB` → `$ORCHESTRATOR_HOME/metrics.duckdb`) and threads it into `record()` → `_compute_cost_usd`. The two additional callers in `bin/orchestrator` (`_ingest_driver_main:337`, `_ingest_subagents_main:473`) pass their already-open `db`. This preserves the existing contract that `record()` writes `cost_usd` into state.yaml step_history (enforced by `test_record_cost_compute.py::test_preserves_existing_cost_usd`) without opening a second DuckDB handle anywhere. Alternatives considered: (A) move compute to `bin/orchestrator:_record_main` — rejected, the `record` dispatch branch does not open `_db`; (C) open a connection inside `_compute_cost_usd` — rejected, risks concurrent handles in `_ingest_subagents_main`. See design.md §4 for the full matrix.
