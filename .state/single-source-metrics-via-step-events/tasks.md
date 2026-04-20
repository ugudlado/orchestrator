# Tasks — Single-Source Metrics via Step Events

<!-- Status markers: [ ] pending, [→] in-progress, [x] done, [~] skipped -->
<!-- TDD pairs: RED test before GREEN implementation. -->
<!-- Format contract: config/steps/contracts/artifact-formats.md § Task Format Contract -->

- [x] T-1: Write failing test for `step_events.turns` column migration + upsert passthrough
  Verify: `pytest config/scripts/orchestrator_next/tests/test_upsert_turns.py` FAILS (red); test asserts (a) `DESCRIBE step_events` includes `turns` after `ensure_schema`, (b) `upsert_step_event` with `usage={"turns": 42, ...}` writes 42 to the column, (c) `_migrate_step_events` adds the column to a pre-existing legacy table without error

- [x] T-2: Add `turns BIGINT` to `step_events` — DDL, migration guard, INSERT_OR_REPLACE, upsert_step_event + upsert_synthetic_event passthrough
  Verify: `pytest config/scripts/orchestrator_next/tests/test_upsert_turns.py` PASSES (green); existing tests in `config/scripts/orchestrator_next/tests/` still pass
  depends: T-1

- [x] T-3: Write failing test for `_totals()` cache/turns/gross_usd/model/pricing projection
  Verify: `pytest config/scripts/orchestrator_next/tests/test_totals_wide.py` FAILS (red); test seeds a feature with model=claude-sonnet-4-5 and asserts totals dict contains `cache_creation_input_tokens`, `cache_read_input_tokens`, `turns`, `gross_usd` (computed from pricing.yaml), `model`, and `pricing` sub-dict with the four rate keys
  depends: T-2

- [x] T-4: Widen `_totals()` SELECT; add dominant-model query; load pricing.yaml for gross_usd + pricing.* attachment
  Verify: `pytest config/scripts/orchestrator_next/tests/test_totals_wide.py` PASSES; `orchestrator cost --change-id X --format json` manually inspected — totals block includes new keys; no removed keys (superset)
  depends: T-3

- [x] T-5: Write failing test for `feature_metrics` table DDL + idempotent migration
  Verify: `pytest config/scripts/orchestrator_next/tests/test_feature_metrics_ddl.py` FAILS (red); test asserts (a) `ensure_schema` creates `feature_metrics` with expected columns, (b) calling `ensure_schema` twice is idempotent, (c) `upsert_feature_metrics` INSERT OR REPLACE keyed on (repo_root, change_id) works
  depends: T-2

- [x] T-6: Add `_DDL_FEATURE_METRICS`, `_INSERT_FEATURE_METRICS`, `upsert_feature_metrics()`, wire into `ensure_schema`
  Verify: `pytest config/scripts/orchestrator_next/tests/test_feature_metrics_ddl.py` PASSES; `DESCRIBE feature_metrics` in a fresh metrics.duckdb shows all columns from design.md §Components #3
  depends: T-5

- [x] T-7: Write failing test for `orchestrator metrics --change-id X --format json` subcommand JSON shape
  Verify: `bash config/tests/test-orchestrator-metrics-json-shape.sh` FAILS (red); test seeds step_events + feature_metrics + feature_complexity for one change, invokes the CLI, and asserts JSON contains every key the metrics-schema.md field registry marks required for schema=feature
  depends: T-6

- [x] T-8: Implement `orchestrator metrics` subcommand + `metrics_report.aggregate_metrics()` composition
  Verify: `bash config/tests/test-orchestrator-metrics-json-shape.sh` PASSES; running `orchestrator metrics --change-id <real-archived-slug> --format json` returns a non-empty dict with keys matching metrics-schema.md; `orchestrator cost --format json` still works unchanged (regression)
  depends: T-7

- [x] T-9: Write failing test for `ingest-feature-metrics` step with fixture tasks.md + state.yaml
  Verify: `bash config/scripts/__tests__/test-ingest-feature-metrics.sh` FAILS (red); test uses a fixture state.yaml (with schema=feature, step_history, completed_at) + fixture tasks.md, runs the Python step, asserts one feature_metrics row is upserted with tasks_total/completed/resolve_rate/churn fields populated
  depends: T-6

- [x] T-10: Implement `config/steps/ingest-feature-metrics.yaml` contract + `scripts/inline/ingest-feature-metrics.py` (port parse_tasks / compute_retries / run_git_churn / extract_review_scores / wall_clock_minutes from compute-swe-metrics.sh to Python)
  Verify: `bash config/scripts/__tests__/test-ingest-feature-metrics.sh` PASSES; missing tasks.md causes non-zero exit (UC-E1 fail-loud); `orchestrator record` successfully writes the step_history entry
  depends: T-9

- [x] T-11a: Extend `test-complete-phase-order.sh` with `POS_INGEST` assertions asserting `POS_MARK < POS_INGEST < POS_METRICS`
  Verify: `bash config/tests/test-complete-phase-order.sh` FAILS (red) — `ingest-feature-metrics` is not yet in `_complete-phase.yaml`
  depends: T-10

- [x] T-11b: Insert `ingest-feature-metrics` into `_complete-phase.yaml` between `mark-change-completed` and `compute-swe-metrics`
  Verify: `bash config/tests/test-complete-phase-order.sh` PASSES; `yq '.steps' config/workflows/_complete-phase.yaml` shows 7 steps in correct order; `_complete-phase-spike.yaml` unchanged (diff is empty)
  depends: T-11a

- [x] T-12: Write failing byte-compat test for `compute-swe-metrics.sh` rewrite
  Verify: `bash config/scripts/__tests__/compute-swe-metrics-projection.test.sh` FAILS (red); test uses a golden archived state.yaml fixture, asserts the output YAML keys match metrics-schema.md field set and integer token values are identical to what the legacy script produced for the same fixture
  depends: T-8

- [x] T-13: Rewrite `scripts/inline/compute-swe-metrics.sh` as thin projection (~50 lines) over `orchestrator metrics --format json`; emit `metrics.source: "duckdb@<ts>"`
  Verify: `bash config/scripts/__tests__/compute-swe-metrics-projection.test.sh` PASSES; file line count < 80; script contains no `jq`/JSONL parsing of `~/.claude/projects`, no `git log`, no `tasks.md` reads
  depends: T-12

- [x] T-14: Write failing narrow-contract test for `read-sub-state-metrics.sh` rewrite
  Verify: `bash config/scripts/__tests__/read-sub-state-metrics.test.sh` FAILS (red); test asserts output YAML contains exactly three top-level keys under `metrics:` — `tokens.total`, `duration_ms`, `churn.files_changed` — matching what `autopilot-session-rollup.sh` reads
  depends: T-8

- [x] T-15: Rewrite `config/scripts/read-sub-state-metrics.sh` as thin projection (~30 lines) over `orchestrator metrics --format json`; preserve narrow output contract
  Verify: `bash config/scripts/__tests__/read-sub-state-metrics.test.sh` PASSES; file line count < 50; `autopilot-session-rollup.sh` successfully consumes the output (integration smoke)
  depends: T-14

- [x] T-16: Write failing test for `register-repo.sh` step_history usage invariant
  Verify: `bash config/tests/test-register-repo-usage-invariant.sh` FAILS (red); test feeds a state.yaml containing one valid step_history row and one row with `agent: developer, status: completed, usage: {}` (no total_tokens), asserts only one row ends up in the `step_history` DuckDB table and a warning is emitted to stderr
  depends: T-2

- [x] T-17: Implement the invariant in `register-repo.sh` around the step_history loop (reject + warn when agent != null AND agent != inline AND status = completed AND total_tokens IS NULL)
  Verify: `bash config/tests/test-register-repo-usage-invariant.sh` PASSES; existing `register-repo` smoke tests still pass
  depends: T-16

- [x] T-18: Fix 5 broken test paths (`config/scripts/compute-swe-metrics.sh` → `scripts/inline/compute-swe-metrics.sh`)
  Verify: `grep -rn "config/scripts/compute-swe-metrics.sh" config/` returns no matches; each of the 5 test scripts listed in spec.md FR-12 is runnable (executes to a real pass/fail, does not silently skip)
  depends: T-13

- [x] T-19: End-to-end integration test — seeded DuckDB + fresh ingest + orchestrator metrics read-back
  Verify: `bash config/tests/test-metrics-pipeline-integration.sh` PASSES; test seeds step_events for one feature, runs `ingest-feature-metrics` against a matching fixture state.yaml+tasks.md, calls `orchestrator metrics --format json`, asserts the output covers all feature-schema required fields from metrics-schema.md with non-null values
  depends: T-11b, T-13, T-17

- [x] T-20: Phase gate — full verify commands + review checkpoint
  Verify: `bash config/scripts/verify-all.sh` PASSES; `pytest config/scripts/orchestrator_next/tests/` PASSES; all tests listed above pass together in a single run; no new lint or type-check warnings introduced
  depends: T-18, T-19
- [x] T-21: Expand compute_retries() in ingest-feature-metrics.py to derive pass_at_1, pass_at_2, regressions, regression_rate from state.yaml retries data
  Verify: bash config/tests/test-metrics-pipeline-integration.sh reports 0 failures
- [x] T-22 (closed-no-bug): investigated gross_usd < net_usd in our own feature; root cause was missing driver-loop ingest, not a formula error. Verified gross >= net once driver-loop row present. No code change needed.
  Verify: orchestrator metrics --change-id <any> returns gross_usd >= net_usd; test_totals_wide.py extended with assertion; design.md §Components #2 formula matched

- [x] T-23: Write failing test for complete-phase ingest-driver auto-invoke
  Verify: `bash config/tests/test-ingest-driver-auto.sh` FAILS (red); test seeds a fake ~/.claude/projects/<slug>/<session>.jsonl, runs the new `ingest-driver` inline step, asserts one `agent_name='driver-loop'` row appears in step_events for the change_id
  depends: T-10

- [x] T-24: Implement ingest-driver auto-invoke step + wire into _complete-phase.yaml
  Verify: T-23 test passes; inline step invokes `orchestrator ingest-driver` with resolved session_id (primary: parse from TMPDIR env; fallback: newest JSONL within feature time window in ~/.claude/projects/<slug>/); fails soft if session_id unresolvable (logs warning, exits 0, does NOT block archive); wired in _complete-phase.yaml BEFORE ingest-feature-metrics so driver-loop row is visible to downstream; _complete-phase-spike.yaml unchanged
  depends: T-23

- [x] T-25: Restore `api_calls` and `per_tool_uses` top-level fields to orchestrator metrics output
  Verify: `orchestrator metrics --change-id X --format json` output includes `api_calls` (value = `turns` alias) and `per_tool_uses` (tool-call aggregate keyed by tool name); byte-compat with legacy compute-swe-metrics.sh output schema (only new-only field in new output is `source`); test-orchestrator-metrics-json-shape.sh extended with both assertions
