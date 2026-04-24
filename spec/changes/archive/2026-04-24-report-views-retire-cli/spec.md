---
feature-id: report-views-retire-cli
linear-ticket: null
---

# Specification: Report views retire CLI — Phase 3 of workflow-engine-as-state-machine

## Motivation

Phase 1 (`pricing-table-in-duckdb`) moved a read-side source of truth into DuckDB and landed the migration runner. Phase 2 (`durable-intent-and-resume`) moved dispatch intent into DuckDB, introducing in-progress rows with NULL `cost_usd`. Phase 3 finishes the read side: every metrics/cost rollup today performed by ~300 lines of Python projection code (`metrics_report.py` entirely, and the aggregation half of `cost_report.py`) becomes a DuckDB view — queryable by the `duckdb` CLI, by any thin shell consumer, and by downstream tools without a Python intermediary. Net outcome: ~300 lines deleted, a single SQL source of truth for feature/phase/agent/repo rollups, the `orchestrator metrics` and `orchestrator cost` subcommands retired, and the end-state `next` + `done` CLI surface one phase closer.

## What Changes

- **Added**: migration `0002_report_views.sql` creating four DuckDB views — `feature_report`, `phase_report`, `agent_report`, `repo_report`.
- **Added**: `scripts/cost-report.sh` — shell wrapper invoked by the `/orchestrate` SKILL at workflow-complete, emitting a markdown cost summary sourced from the views via `duckdb -json` + inline `python3 -c` formatting.
- **Rewritten**: `scripts/inline/compute-swe-metrics.sh` — queries `feature_report` via `duckdb -json -readonly` instead of shelling out to `orchestrator metrics`. Output YAML shape is byte-equivalent to the current version on the frozen baseline (D-3, D-4).
- **Rewritten**: `config/scripts/read-sub-state-metrics.sh` — queries `feature_report` directly for the narrow `tokens.total` / `duration_ms` / `churn.files_changed` projection. Same shape as today.
- **Updated**: `skills/orchestrate/SKILL.md` — replace `orchestrator cost --change-id` invocation at workflow-complete with `scripts/cost-report.sh --change-id`.
- **Retired**: `_metrics_main` and `_cost_main` in `bin/orchestrator`; the `metrics` and `cost` subcommands are removed from the dispatcher and the usage banner.
- **Deleted**: `config/scripts/orchestrator_next/metrics_report.py` in full.
- **Trimmed**: `config/scripts/orchestrator_next/cost_report.py` retains only `_anomalies()` and `_step_allowlist_anomalies()` as standalone callable Python helpers (no CLI surface); all `_totals`, `_per_phase`, `_per_agent`, `_per_model`, `_by_*`, `_by_complexity`, `aggregate_*`, and `render_markdown_*` functions are deleted. `render_markdown_feature` is kept only if the inline markdown formatter in `scripts/cost-report.sh` cannot reproduce the 8-section report byte-for-byte — decision made at the task gate (T-9), not at design time.
- **Retargeted**: `config/scripts/tests/test_cost_cli.py` is **deleted in full**. Shape-level assertions move to a new `config/scripts/orchestrator_next/tests/test_report_views.py` (SQL-level tests using the `in_memory_db` fixture pattern). Slug-guard and flag-validation tests have no target after retirement and are not retained.
- **Retargeted**: `config/scripts/__tests__/compute-swe-metrics-projection.test.sh` and `config/scripts/__tests__/read-sub-state-metrics.test.sh` — assertions now verify the view-sourced output; RED phase captures a byte-equivalence baseline from a replayed archived state.

## Requirements

### Functional

1. **FR-1 (view DDL)**: Migration `0002_report_views.sql` creates exactly four views — `feature_report`, `phase_report`, `agent_report`, `repo_report` — using `CREATE OR REPLACE VIEW`. Every column referenced by `aggregate_metrics()` and `aggregate_feature()` today is reproducible from these four views (plus direct `step_events` / `tool_calls` queries inside `scripts/cost-report.sh` for the per-step and per-model sections).
2. **FR-2 (feature_report coverage)**: `feature_report` includes — for every `(repo_root, change_id)` present in `step_events` — totals (cost_usd, gross_usd, input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens, total_tokens, turns, duration_ms, step_count, rework_ratio, model, tool_calls_count), complexity / schema_name (via LEFT JOIN on `feature_complexity` and `feature_metrics`), all `feature_metrics` columns projected through (resolution, churn, retries, reviews, wall_clock_minutes), benchmarks (cost_per_task_usd, cost_per_resolution_usd, tokens_per_task, tokens_per_resolution, input_output_ratio, cache_hit_rate), and stringified JSON columns `per_agent_tokens`, `per_agent_tools`, `per_tool_uses`, `per_step`. Pricing rates for the dominant model (via `pricing` table) are exposed as four columns `pricing_input_usd` / `pricing_output_usd` / `pricing_cache_read_usd` / `pricing_cache_creation_usd`.
3. **FR-3 (phase_report coverage)**: `phase_report` includes, per `(repo_root, change_id, phase)`: `cost_usd`, `input_tokens`, `output_tokens`, `duration_ms`, `step_count`, `first_seen` (MIN(started_at)). Ordered by `first_seen ASC, phase ASC`.
4. **FR-4 (agent_report coverage)**: `agent_report` includes, per `(repo_root, change_id, agent_name)`: `cost_usd`, `input_tokens`, `output_tokens`, `duration_ms`, `step_count`. Ordered by `agent_name ASC`.
5. **FR-5 (repo_report coverage)**: `repo_report` includes, per `(repo_basename, change_id)`: `cost_usd`, `input_tokens`, `output_tokens`, `step_count`, `first_seen`. `repo_basename` is extracted via `regexp_extract(repo_root, '[^/]+$')`. Ordered by `first_seen ASC, change_id ASC`. (The `--since` and `--by complexity` variants have zero non-CLI callers per discovery and are **not** exposed as views; later phases reusing `repo_report` can filter on `first_seen` downstream if needed.)
6. **FR-6 (shape stability)**: The YAML block emitted by `compute-swe-metrics.sh` and consumed by `complete` phase's prediction-accuracy step has identical top-level keys, nested keys, and value encodings (strings-as-JSON for `per_agent_*`, `per_tool_uses`, `per_step`) as the current implementation. Verified by byte-equivalence test on the frozen baseline.
7. **FR-7 (CLI retirement)**: `bin/orchestrator metrics …` and `bin/orchestrator cost …` invocations return exit code 3 with the updated `_usage()` banner — neither subcommand is recognised. `_metrics_main` and `_cost_main` are deleted from `bin/orchestrator`. A grep for `orchestrator metrics` or `orchestrator cost` in production code (excluding `spec/changes/archive/**` and `.state/**`) returns zero hits.
8. **FR-8 (markdown rendering path)**: `skills/orchestrate/SKILL.md`'s dispatch loop at `complete_workflow` invokes `scripts/cost-report.sh --change-id $CHANGE_ID`. That script reads `feature_report` (plus supplementary `step_events GROUP BY model` for the Per-Model section), formats an 8-section markdown report via inline `python3 -c`, and prints to stdout. If the inline formatter's output differs from `render_markdown_feature`'s output by any non-whitespace byte at T-9 gate, `render_markdown_feature` is retained in a slimmed `cost_report.py` as a Python helper invoked by the shell script — else `render_markdown_feature` is deleted.
9. **FR-9 (shell rewrites)**: `scripts/inline/compute-swe-metrics.sh` and `config/scripts/read-sub-state-metrics.sh` run in bash 3.2 (no `declare -A`, `mapfile`, `readarray`, `${var^^}`) and use `duckdb -json -readonly` plus `python3 -c` for output shaping. Both fail with non-zero exit and stderr when `feature_report` returns zero rows for the requested `change_id`.
10. **FR-10 (test retargeting)**: `config/scripts/tests/test_cost_cli.py` is deleted. `config/scripts/orchestrator_next/tests/test_report_views.py` (new) asserts aggregation shapes directly against the views via the shared `in_memory_db` fixture. `config/scripts/__tests__/compute-swe-metrics-projection.test.sh` and `config/scripts/__tests__/read-sub-state-metrics.test.sh` assert output YAML shape against a committed baseline fixture reconstructed from an archived feature's `step_history`.
11. **FR-11 (anomaly preservation)**: `_anomalies()` and `_step_allowlist_anomalies()` in `cost_report.py` remain callable as standalone Python functions (no CLI entry point). Their existing unit tests (`test_cost_report_anomaly.py`) continue to pass. Phase 5 will decide whether to expose them or retire them; this phase does not expand or narrow their scope.

### Non-Functional

1. **NFR-1 (byte-equivalence)**: The YAML output of the rewritten `compute-swe-metrics.sh`, when run against the reconstructed-baseline DB, is byte-identical to the output of the current `compute-swe-metrics.sh` on the same DB. Same byte-equivalence holds for `read-sub-state-metrics.sh`.
2. **NFR-2 (parametrised SQL)**: No SQL inside `0002_report_views.sql`, `scripts/cost-report.sh`, `compute-swe-metrics.sh`, or `read-sub-state-metrics.sh` interpolates user input. The views themselves take no parameters (they are column-level filters applied by the calling query). The shell scripts bind `change_id` via a `-cmd` arg or a `WHERE change_id = ?` pattern delegated through `duckdb`'s parameter-free SQL — slug-guard validation happens in the shell layer (same `^[a-z0-9][a-z0-9-]*$` regex as `_SLUG_RE_BIN`) before the value reaches `duckdb`.
3. **NFR-3 (coverage)**: Coverage on files modified by this phase (the new migration SQL, `scripts/cost-report.sh`, both rewritten shells, `bin/orchestrator` after the deletions, `cost_report.py` after trimming) is ≥ 90%. SQL coverage is demonstrated by `test_report_views.py` exercising every column and every LEFT JOIN branch (present / missing row).
4. **NFR-4 (no new CLI subcommand)**: `bin/orchestrator`'s recognised-verb list shrinks by two (`cost`, `metrics`) and grows by zero. The end-state `next` / `done` (+ the interim `record`, `ingest-*`, `doctor`) surface is preserved unchanged.
5. **NFR-5 (performance budget)**: End-to-end latency of `scripts/cost-report.sh --change-id <cid>` against a representative production DB (full `spec/changes/archive/**` replayed, ~30 features, ~600 step_events rows) is comparable to today's `orchestrator cost --change-id` — specifically within 2× on wall-clock time, measured at phase-gate T-13. This is a production-shaped target, not a synthetic microbenchmark. The views themselves must not introduce a full-table scan where a keyed lookup suffices (verify that queries filtering by `change_id` produce plans using `idx_step_events_change`).

## Architecture

See `design.md` for DDL, file-modification table, and data-flow sketches.

## Test Strategy

### Test File Paths

- `config/scripts/orchestrator_next/migrations/0002_report_views.sql` → `config/scripts/orchestrator_next/tests/test_report_views.py`
- `scripts/cost-report.sh` → `config/scripts/__tests__/cost-report.test.sh` (new)
- `scripts/inline/compute-swe-metrics.sh` → `config/scripts/__tests__/compute-swe-metrics-projection.test.sh` (retargeted)
- `config/scripts/read-sub-state-metrics.sh` → `config/scripts/__tests__/read-sub-state-metrics.test.sh` (retargeted)
- `bin/orchestrator` (usage banner + verb dispatch) → covered by existing `test_doctor.py` style subprocess tests, plus a new regression test asserting exit-3 for `orchestrator cost` / `orchestrator metrics`.
- `config/scripts/orchestrator_next/cost_report.py` (anomaly helpers only) → existing `test_cost_report_anomaly.py` (unchanged).

### Coverage Targets

- Overall ≥ 90% on modified files (NFR-3).
- Every column listed in FR-2, FR-3, FR-4, FR-5 is asserted in at least one test.
- Every zero-division guard (see `design.md § Zero-Division Translation Table`) has a test seeding the denominator = 0 case.
- Every LEFT JOIN branch (feature_metrics present / absent; feature_complexity present / absent) is exercised.
- Byte-equivalence baselines committed as fixtures under `config/scripts/__tests__/fixtures/` — one for `compute-swe-metrics.sh`, one for `read-sub-state-metrics.sh`.

### Key Test Scenarios

- UC-1 happy path: workflow-complete renders 8-section markdown via `scripts/cost-report.sh`, matching the pre-phase output byte-for-byte on the baseline.
- UC-2 happy path: `compute-swe-metrics.sh` emits YAML identical to the baseline; `yaml.safe_load(output)['metrics']['per_agent_tokens']` round-trips to the same dict after `json.loads`.
- UC-3 happy path: `read-sub-state-metrics.sh` emits exactly three top-level keys under `metrics:` (`tokens.total`, `duration_ms`, `churn.files_changed`). No extraneous keys.
- UC-E1: seed step_events with a mix of NULL and numeric `cost_usd` — `feature_report.cost_usd` equals the numeric sum, not NULL.
- UC-E2: run `feature_report` query against a DB where `feature_metrics` has no matching row — view returns one row with NULL resolution/churn/reviews columns, cost/tokens populated from step_events.
- UC-E3: run `compute-swe-metrics.sh` twice on a frozen DB; stdout bytes match.

## Acceptance Criteria

- **AC-1**: Given a DB populated by an archived feature's step_history, when `duckdb -readonly -c "SELECT COUNT(*) FROM feature_report"` runs, then it returns the distinct-(repo_root, change_id) count from `step_events`. [traces: UC-1, UC-4]
- **AC-2**: Given a change_id with one in-progress step_events row (NULL `cost_usd`) and two completed rows (numeric), when `SELECT cost_usd FROM feature_report WHERE change_id = ?` runs, then the value equals the sum of the two numeric rows (NULL is coalesced to 0). [traces: UC-E1]
- **AC-3**: Given a change_id present in `step_events` but absent from `feature_metrics`, when `SELECT * FROM feature_report WHERE change_id = ?` runs, then exactly one row is returned with NULL `resolve_rate`, `files_changed`, `wall_clock_minutes`, etc. [traces: UC-E2]
- **AC-4**: Given the frozen baseline DB, when `bash scripts/inline/compute-swe-metrics.sh <state_dir>` runs twice in succession, then the two stdout streams are byte-identical. [traces: UC-E3]
- **AC-5**: Given the same frozen baseline DB, when the rewritten `compute-swe-metrics.sh` runs and its stdout is compared to the committed baseline fixture (produced by the pre-phase version of the script), then the two are byte-identical. [traces: UC-2, D-3, D-4]
- **AC-6**: Given the same frozen baseline DB, when the rewritten `read-sub-state-metrics.sh` runs and its stdout is compared to the committed baseline fixture, then the two are byte-identical. [traces: UC-3]
- **AC-7**: Given a repo checkout at HEAD post-merge, when `rg -l "orchestrator (cost|metrics)"` runs across `bin/`, `config/`, `scripts/`, `skills/` (excluding `spec/changes/archive/**`, `.state/**`, and this feature's own state dir), then the match count is zero. [traces: FR-7]
- **AC-8**: Given invocation of `bin/orchestrator cost --change-id foo` or `bin/orchestrator metrics --change-id foo`, when the process runs, then it exits 3 and prints the updated usage banner to stderr. [traces: FR-7, NFR-4]
- **AC-9**: Given a seeded DB, when `duckdb -json -readonly -c "SELECT per_agent_tokens FROM feature_report WHERE change_id = 'X'"` runs, then the returned string is a valid JSON object literal parseable by `json.loads`, and `yq -p=json` on the same string yields the expected nested keys. [traces: FR-2, D-8]
- **AC-10**: Given `scripts/cost-report.sh --change-id <cid>` runs against the baseline DB, when the output is diffed against either (a) the inline-formatter output or (b) `render_markdown_feature`'s output (whichever the T-9 gate retained), then the diff is empty. [traces: FR-8]
- **AC-11**: Given `pytest config/scripts/orchestrator_next/tests/test_report_views.py` runs, when all tests pass, then every column in FR-2/FR-3/FR-4/FR-5 has at least one asserting test and every zero-division guard has a denominator-zero test case. [traces: FR-1, FR-2, FR-3, FR-4, FR-5, NFR-3]
- **AC-12**: Given the completed phase, when overall coverage is measured on the files listed under `design.md § File-Modification Table`, then it is ≥ 90%. [traces: NFR-3]

## Alternatives Considered

**Alternative 1: Introduce an `orchestrator_next.report` Python helper module that wraps the SQL**
Rejected. Driver (OQ-7) ruled against it. Adds a second source of truth (Python wrapper + SQL) for the same aggregation. The shell consumers are the only callers; inlining `python3 -c` in the shell script is simpler and removes one import layer.

**Alternative 2: Keep `_metrics_main` and `_cost_main` as thin wrappers over the views**
Rejected. Fails the explicit driver goal of CLI retirement (D-1, D-2). The end-state `next` + `done` surface is the project's north star; expanding the CLI surface is a regression.

**Alternative 3: Expose `per_step` as a row-per-step view instead of a JSON-string column in `feature_report`**
Rejected. `per_step` is consumed in exactly one place — the `metrics:` YAML block in state.yaml — as a single nested map. Breaking it out requires every caller to re-aggregate with a per-call JOIN; keeping it as a `json_group_object` column inside `feature_report` matches the existing string-encoding pattern for `per_agent_tokens` (D-8) and requires no caller-side aggregation.

**Alternative 4: Add a `model_report` view for the Per-Model markdown section**
Rejected. The per-model section is consumed only by `scripts/cost-report.sh`. A direct `SELECT model, SUM(cost_usd), … FROM step_events WHERE change_id = ? GROUP BY model` inside the shell script keeps the view count at four (driver D-1: no new CLI surface implies minimal view surface too).

## Impact

- **Breaking**: `orchestrator metrics` and `orchestrator cost` are no longer valid CLI subcommands. Anyone running these interactively sees an exit-3 + usage error. Migration path: use `duckdb -readonly` against the views, or call `scripts/cost-report.sh` for markdown output.
- **Archive queries preserved**: archived features' `state.yaml` files retain their historical `metrics:` YAML blocks unchanged. No data migration.
- **Downstream consumers unchanged** for `compute-swe-metrics.sh` (prediction-accuracy, ingest-feature-metrics) and `read-sub-state-metrics.sh` (autopilot rollup) — shape is identical (D-3, D-8).

## Decisions

- **D-1 (anomalies, OQ-1)** → Approach C. `_anomalies()` and `_step_allowlist_anomalies()` remain in a trimmed `cost_report.py` as standalone callable Python functions; no CLI entry point. Phase 5 revisits.
- **D-2 (markdown, OQ-2)** → Prefer inline `python3 -c` formatter in `scripts/cost-report.sh`. Retain `render_markdown_feature` only if the inline formatter cannot reproduce byte-equivalent output against the 8-section baseline at T-9 gate. `render_metrics_md` is deleted unconditionally (no caller).
- **D-3 (aggregate_repo variants, OQ-3)** → Drop `--since` and `--by complexity`. The `repo_report` view exposes `first_seen` so future callers can filter on it downstream if needed; no per-complexity variant is provided.
- **D-4 (read-sub-state-metrics.sh, OQ-4)** → In scope. Rewritten alongside `compute-swe-metrics.sh`, with its own byte-equivalence fixture.
- **D-5 (baseline fixture, OQ-5)** → Use `spec/changes/archive/2026-04-21-durable-intent-and-resume/state.yaml` as the source of truth. A setup task reconstructs a DuckDB file by replaying that state's `step_history` through `upsert_step_event`, then runs the pre-phase `compute-swe-metrics.sh` and `read-sub-state-metrics.sh` against it to capture the baseline bytes.
- **D-6 (JSON encoding, OQ-6)** → Views emit `per_agent_tokens` / `per_agent_tools` / `per_tool_uses` / `per_step` as stringified JSON via `json_group_object(k, v)::VARCHAR`. The shell consumer's `python3 -c` re-dumps with `sort_keys=True` to guarantee deterministic key order across runs (bypasses any non-deterministic ordering inside DuckDB's aggregate).
- **D-7 (no Python wrapper module, OQ-7)** → No `orchestrator_next.report` module. Shell + SQL only.
- **D-8 (per_step location)** → Inside `feature_report` as a stringified JSON column. No separate per-step view.
- **D-9 (per_model location)** → Not exposed as a view. `scripts/cost-report.sh` queries `step_events GROUP BY model` directly when producing the Per-Model markdown section.

<!-- Format contract: contracts/artifact-formats.md § Specification Format Contract -->
