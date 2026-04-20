# Tasks: Pricing table in DuckDB — Phase 1 of workflow-engine-as-state-machine

<!-- TDD: every implementation (GREEN) is preceded by its test task (RED). -->
<!-- Files listed on tasks reference the spec/design file-modification table. -->

## Phase 1 — Migration runner

- [x] T-1 Write tests: `_run_migrations(db)` in `upsert.py` (RED)
  - **Why**: FR-1, FR-2, NFR-2. Covers AC-1, AC-2.
  - **Files**: `config/scripts/orchestrator_next/tests/test_migrations.py` (new)
  - **Scenarios**: (a) fresh DB → `schema_migrations` created, no rows; (b) with a test `0001_noop.sql` fixture under a tmp migrations dir, runner applies it and records the name; (c) running twice applies no-op; (d) a broken `.sql` raises and `schema_migrations` stays unchanged; (e) adding `0002_noop.sql` to the tmp dir → only 0002 applied on next call.
  - **Verify**: pytest runs, every test FAILS (red) because `_run_migrations` does not yet exist.

- [x] T-2 Implement: `_run_migrations(db)` in `upsert.py` (GREEN) (depends: T-1)
  - **Why**: FR-1, FR-2, NFR-2. Same requirements as T-1.
  - **Files**: `config/scripts/orchestrator_next/upsert.py` (modify); `config/scripts/orchestrator_next/migrations/` (create empty dir + `.gitkeep`)
  - **Approach**: add `_DDL_SCHEMA_MIGRATIONS` constant, `_migrations_dir()` helper, `_run_migrations(db)` function — all as module-level siblings to `_migrate_step_events` / `_migrate_tool_calls`. Call `_run_migrations(db)` from `ensure_schema` after `_DDL_FEATURE_METRICS`. Follow the existing parameterised-SQL style in `upsert.py`.
  - **Verify**: all T-1 tests pass; existing `test_upsert*.py` + `test_feature_metrics_ddl.py` still pass; `python -c "from orchestrator_next.upsert import _run_migrations"` imports clean.

## Phase 2 — Pricing table + seed

- [x] T-3 Write tests: seed migration `0001_seed_pricing.sql` (RED) (depends: T-2)
  - **Why**: FR-2, NFR-5. Covers AC-1, AC-9 (step_events unchanged).
  - **Files**: `config/scripts/orchestrator_next/tests/test_migrations.py` (extend)
  - **Scenarios**: (a) after `ensure_schema` on a fresh DB, `DESCRIBE pricing` returns exactly the spec columns; (b) `SELECT COUNT(*) FROM pricing` equals the current `config/pricing.yaml` model count + 1 (`__default__`); (c) spot-check one row (e.g. `claude-sonnet-4-6` input=3.00, output=15.00, cache_read=0.30, cache_creation=3.75); (d) `is_local=TRUE` for `coder`; (e) `DESCRIBE step_events` is unchanged from pre-migration baseline.
  - **Verify**: tests FAIL (red) because the seed file does not yet exist.

- [x] T-4 Implement: `0001_seed_pricing.sql` (GREEN) (depends: T-3)
  - **Why**: FR-2, NFR-5.
  - **Files**: `config/scripts/orchestrator_next/migrations/0001_seed_pricing.sql` (new)
  - **Approach**: `CREATE TABLE IF NOT EXISTS pricing (...)` with PK `(model_id, effective_from)`, followed by one `INSERT OR REPLACE` statement containing every row from the current `config/pricing.yaml` plus `__default__`, all with `effective_from = '2025-01-01T00:00:00'`. DDL header comment: "timestamps stored UTC by convention".
  - **Verify**: all T-3 tests pass; the SQL file loads cleanly via `duckdb :memory: < 0001_seed_pricing.sql`.

## Phase 3 — `_lookup_price` + `_compute_cost_usd` rewire

- [x] T-5 Write tests: `_lookup_price` + cost compute on DuckDB (RED) (depends: T-4)
  - **Why**: FR-3, NFR-1. Covers AC-3, AC-4.
  - **Files**: `config/scripts/orchestrator_next/tests/test_pricing_lookup.py` (new); `config/scripts/orchestrator_next/tests/test_record_cost_compute.py` (modify)
  - **Scenarios (test_pricing_lookup.py)**: exact model hit; unknown model → `__default__` fallback; two rows for same model, different `effective_from` → latest wins; `__default__` also absent → function returns `None` and warns. Include a micro-benchmark: 1000 lookups finish under 50 ms on CI hardware (NFR-1).
  - **Scenarios (test_record_cost_compute.py)**: replace `monkeypatch.setenv(ORCHESTRATOR_HOME, ...)` + `_load_pricing.cache_clear()` with an `in_memory_db` fixture that runs `ensure_schema(db)`. Tests call `record()` directly and pass the fixture's `db` (new `db=` kwarg on `record()`). Retain every existing assertion: `claude-sonnet-4-6` → 0.141000; unknown model → `__default__`; `test_preserves_existing_cost_usd` still passes. Add a NEW scenario: `record(state_yaml_path, payload, db=None)` (offline/test mode with no DB) → `usage.cost_usd` remains unset, stderr warning printed, no exception.
  - **Verify**: tests FAIL (red) — `_lookup_price` does not exist; `_compute_cost_usd` does not accept a `db` argument yet; `record()` does not accept a `db` kwarg yet.

- [x] T-6 Implement: `_lookup_price` + `_compute_cost_usd(db, agent, usage)` + three-caller fan-out (GREEN) (depends: T-5)
  - **Why**: FR-3.
  - **Files**: `config/scripts/orchestrator_next/record.py` (modify — `_compute_cost_usd`, `record()`, `main()`); `bin/orchestrator` (modify TWO call sites — `_ingest_driver_main` ~line 337 and `_ingest_subagents_main` ~line 473).
  - **Approach**:
    1. In `record.py`: delete `_load_pricing` lru_cache loader. Add `_lookup_price(db, model_id, effective_at)` that runs the parameterised SELECT (`WHERE model_id = ? AND effective_from <= ? ORDER BY effective_from DESC LIMIT 1`), with `__default__` fallback and stderr warning + return `None` when both miss or `db is None`. Change `_compute_cost_usd` signature to `(db, agent, usage, *, now=None)`.
    2. In `record.py` `record()` function: accept optional `db=None` kwarg; thread it to `_compute_cost_usd` at the existing call site (~line 394).
    3. In `record.py` `main()`: resolve `METRICS_DB` then `$ORCHESTRATOR_HOME/metrics.duckdb`; if resolved, open `duckdb.connect(db_path)`, call `ensure_schema(db)` (idempotent; safe if already applied), pass `db` into `record()`; `db.close()` in `finally`. If no path resolves, pass `db=None` (fail-open; stderr warning emitted by `_compute_cost_usd`).
    4. In `bin/orchestrator` `_ingest_driver_main`: MOVE the `_compute_cost_usd("driver-loop", usage)` call from its current position (~line 337, before `db = duckdb.connect(...)` at line 344) to INSIDE the existing `try:` block (after `ensure_schema(db)` ~line 346, before `upsert_synthetic_event`). Call becomes `_compute_cost_usd(db, "driver-loop", usage)`.
    5. In `bin/orchestrator` `_ingest_subagents_main`: at the existing `_compute_cost_usd(agent_name, usage)` call (~line 473), `db` is already in scope (opened earlier in the function). Prepend `db`: `_compute_cost_usd(db, agent_name, usage)`.
  - **Verify**: all T-5 tests pass; `test_record_cost_compute.py` regression suite is green (including `test_preserves_existing_cost_usd`); `rg '_load_pricing\b' config/scripts/orchestrator_next/` returns no hits; `rg '_compute_cost_usd\(' bin/orchestrator config/scripts/orchestrator_next/` shows exactly three call sites, all passing `db` (or `db` + args) as first argument; `bin/orchestrator record <state.yaml>` end-to-end smoke test writes `cost_usd` to state.yaml step_history identical to pre-change value for a fixed payload.

## Phase 4 — cost_report.py migration

- [x] T-7 Write tests: `_load_pricing_for_model(db, model)` (RED) (depends: T-6)
  - **Why**: FR-4. Covers AC-6 (read-only DB without pricing table).
  - **Files**: `config/scripts/orchestrator_next/tests/test_totals_wide.py` (modify); `config/scripts/orchestrator_next/tests/test_cost_report.py` (modify if needed)
  - **Scenarios**: (a) seeded DB + known model → returns dict with expected rates; (b) seeded DB + unknown model → `__default__` rates; (c) DB with NO `pricing` table (simulate legacy DB: `ensure_schema` skipped) opened `read_only=True` → function returns the conservative built-in fallback dict, does NOT raise; (d) `gross_usd` regression check: aggregation output identical to pre-migration for a fixed `step_events` fixture.
  - **Verify**: tests FAIL (red) because `_load_pricing_for_model` still takes `(model)` not `(db, model)`.

- [x] T-8 Implement: `_load_pricing_for_model(db, model)` → SQL with fallback + scrub stale `_load_pricing` import (GREEN) (depends: T-7)
  - **Why**: FR-4. Closes F-3 from review-specify.md.
  - **Files**: `config/scripts/orchestrator_next/cost_report.py` (modify)
  - **Approach**:
    1. Delete the line `from orchestrator_next.record import _load_pricing` at cost_report.py:71 and the line `pricing = _load_pricing()` at :72. These would raise `ImportError` after T-6 deletes `_load_pricing` from `record.py`, silently degrading every lookup to the fallback dict via the enclosing try/except.
    2. Change `_load_pricing_for_model` signature to `(db, model)`. Body: parameterised SELECT against `pricing`, fallback to `__default__`, wrap in `try/except duckdb.Error` returning the hard-coded `{input:15.0, output:75.0, cache_read:1.5, cache_creation:18.75}` dict on any DB error.
    3. Add `# TODO(phase-N): temporal-correctness — switch to effective_from <= step ts when dashboards are ready` at the call site.
  - **Verify**: all T-7 tests pass; `test_cost_report*.py` existing suite green; `rg '_load_pricing\b' config/scripts/orchestrator_next/cost_report.py` returns ZERO hits (covers both the deleted import and the deleted call); `rg 'from orchestrator_next\.record import' config/scripts/orchestrator_next/cost_report.py` returns zero hits; `python -c "from orchestrator_next import cost_report"` imports clean after T-6 changes.

## Phase 5 — estimate-cost.sh rewrite

- [x] T-9 Write tests: `estimate-cost.sh` parity (RED) (depends: T-8)
  - **Why**: FR-6. Covers AC-5.
  - **Files**: `scripts/inline/tests/test_estimate_cost_sh.sh` (new) OR `config/scripts/orchestrator_next/tests/test_estimate_cost_sh.py` (new, preferred — subprocess-driven)
  - **Scenarios**: (a) capture current (pre-rewrite) stdout+stderr against a fixed `state_dir` + fixed archive → save as `fixtures/estimate_cost_before.txt`; (b) after rewrite, run the same invocation with a seeded DuckDB DB → assert byte-identical output (or numerically equivalent to 6 decimals if timestamp lines differ); (c) with `ORCHESTRATOR_DB=/nonexistent` → script emits default rates and completes zero-exit; (d) **bash 3.2 regression guard**: explicitly invoke `/bin/bash config/scripts/estimate-cost.sh …` on macOS (where `/bin/bash` is 3.2.x) — or equivalently `env BASH_COMPAT=32 bash …` on Linux — and assert exit 0 with no "declare -A" / "bad substitution" / associative-array errors in stderr. This is the same failure mode that killed the current AWK version in a prior session (see preview-route state.yaml). Rationale: the rewrite uses only `[[ -f ]]`, `local`, `${var//old/new}`, pipes, and `python3 -c`, all of which are bash 3.2 compatible; scenario (d) locks that in.
  - **Verify**: tests FAIL (red) — the rewrite has not happened, so either the rewrite test (b) is absent of DB, or the parity diff is trivially zero but the DB-query path is untested.

- [x] T-10 Implement: `estimate-cost.sh` shells out to `duckdb -json` (GREEN) (depends: T-9)
  - **Why**: FR-6.
  - **Files**: `config/scripts/estimate-cost.sh` (modify)
  - **Approach**: replace the `lookup_pricing()` AWK function body with a `duckdb -readonly -json "$DB" "SELECT …"` invocation, parse the JSON via an inline `python3 -c '...'` one-liner (Python is already a project dep), keep the default-rate fallback (`echo "15.00 75.00 1.50"`) for DB-absent / row-absent cases. Remove the `PRICING_FILE` variable and the reference to `$ORCHESTRATOR_HOME/config/pricing.yaml`. MUST NOT introduce `declare -A`, `${var^^}`, `mapfile`, or any other bash-4+ construct (scenario T-9(d) enforces this).
  - **Verify**: T-9 tests pass, including the bash 3.2 scenario (d); `rg 'pricing.yaml' config/scripts/estimate-cost.sh` returns zero hits; `bash -n config/scripts/estimate-cost.sh` clean; `rg 'declare -A|\\${[A-Za-z_]+\\^\\^|mapfile' config/scripts/estimate-cost.sh` returns zero hits.

## Phase 6 — Ingestion script

- [x] T-11 Write tests: `scripts/ingest-pricing.py` (RED) (depends: T-4)
  - **Why**: FR-5. Covers AC-7.
  - **Files**: `scripts/tests/test_ingest_pricing.py` (new)
  - **Scenarios**: (a) happy path: run script against a seeded temp DB with `--model foo --input-usd 1.0 --output-usd 2.0 --cache-read-usd 0.1 --cache-creation-usd 1.25 --effective-from 2026-06-01T00:00:00` → exit 0, row present, stdout is `inserted foo @ 2026-06-01T00:00:00`; (b) duplicate `(model_id, effective_from)` → exit non-zero, stderr mentions "duplicate"; (c) `--input-usd -1.0` → validation error, exit non-zero before any DB call; (d) `--help` output contains a worked-example invocation line; (e) `python scripts/ingest-pricing.py --help` runs to completion with exit 0 and emits NO `ImportError` / `ModuleNotFoundError` on stderr — both with `ORCHESTRATOR_HOME` set AND with it unset (import-path resilience, F-5).
  - **Verify**: tests FAIL (red) — the script does not exist.

- [x] T-12 Implement: `scripts/ingest-pricing.py` (GREEN) (depends: T-11)
  - **Why**: FR-5.
  - **Files**: `scripts/ingest-pricing.py` (new)
  - **Approach**: mirror `scripts/inline/ingest-feature-metrics.py` lines 33–37 — resolve `ORCHESTRATOR_HOME` (env var; else walk up from `__file__` to the repo root), then `sys.path.insert(0, os.path.join(ORCHESTRATOR_HOME, "config", "scripts"))` BEFORE any `from orchestrator_next...` import. Then argparse with `--model`, `--input-usd`, `--output-usd`, `--cache-read-usd`, `--cache-creation-usd`, `--is-local` (store_true), `--effective-from` (default UTC now ISO), `--db` (default `$HOME/.state/orchestrator.duckdb` — match existing scripts). Validate rates ≥ 0. Open DB RW, call `ensure_schema(db)` (guarantees pricing table), parameterised INSERT, commit. Catch `duckdb.ConstraintException` → emit "duplicate (model_id, effective_from)" to stderr and exit 2. `--help` epilog includes a worked example.
  - **Verify**: all T-11 tests pass (including import-resilience scenario (e)); `python scripts/ingest-pricing.py --help` shows the example and exits 0; chmod +x set.

## Phase 7 — Delete pricing.yaml (ORDERED LAST)

- [x] T-13 Review checkpoint (phase gate) (depends: T-2, T-4, T-6, T-8, T-10, T-12)
  - **Why**: Gate before deletion — every consumer must be migrated and tested.
  - **Verify**: `pytest config/scripts/orchestrator_next/tests/ scripts/tests/` passes; coverage on `upsert.py`, `record.py`, `cost_report.py`, `ingest-pricing.py` ≥ 90%; `rg 'pricing\.yaml' config/ scripts/ bin/ --type-not md` shows only deletion-pending references (i.e. comments to be removed).

- [x] T-14 Delete `config/pricing.yaml` and scrub remaining references (depends: T-13)
  - **Why**: FR-7. Covers AC-8.
  - **Files**: `config/pricing.yaml` (delete); any stale comments in `record.py`, `cost_report.py`, `estimate-cost.sh` that mention pricing.yaml.
  - **Approach**: `git rm config/pricing.yaml`; replace any surviving prose references in docstrings/comments with "the DuckDB `pricing` table"; do NOT touch `spec/` archive files.
  - **Verify**: `rg -l 'pricing\.yaml' config/ scripts/ bin/ --type-not md` returns zero hits; full test suite passes; `git diff --stat` shows only the YAML deletion + comment scrubs.

- [x] T-15 Final review checkpoint (phase gate) (depends: T-14)
  - **Verify**: `pytest` full suite green; `orchestrator next` smoke test (via preview-route) produces the same `route_preview:` YAML shape as before; `_lookup_price` micro-benchmark from T-5 reported ≤ 2× of the YAML+lru_cache baseline captured during T-5.

<!-- Status markers: [ ] pending, [→] in-progress, [x] done, [~] skipped -->
<!-- (depends: T-xxx) = dependency -->
<!-- TDD: test tasks (RED) always precede implementation tasks (GREEN) -->
<!-- Coverage target: ≥ 90% at each phase gate -->
