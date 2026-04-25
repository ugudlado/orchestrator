# Tasks — Absorb ingest-feature-metrics into `done` (Phase 5)

<!--
  Sequencing: Stage A (additive) → Stage B (deletion).
  TDD: every implementation task (GREEN) has a preceding test task (RED).
  Format per artifact-formats.md § Task Format Contract:
    "- [ ] T-N: description" + indented "  Verify:" + optional "  depends:".
-->

## Stage A — additive (helpers + trigger + parity test)

- [x] T-1: Write tests for the 6 computation functions ported from `ingest-feature-metrics.py` (RED). FR-1 — `parse_tasks`, `compute_retries`, `compute_resolution`, `run_git_churn`, `extract_review_scores`, `wall_clock_minutes` must produce identical output to the legacy script for the same inputs.
  Verify: `pytest config/scripts/orchestrator_next/tests/test_feature_metrics_compute.py -x` runs and FAILS with `ImportError` (functions not yet in `record.py`). Tests cover: (a) `parse_tasks` counts `[x]`/`[ ]`/`[~]` markers; (b) `compute_retries` sums `state.retries.*` and reads `state.human_interventions`; (c) `compute_resolution` returns all-None when `tasks_total` is None or 0 and produces monotonic `pass_at_2 >= pass_at_1` otherwise; (d) `run_git_churn` returns the all-zeros default when subprocess fails or returns no commits; (e) `extract_review_scores` averages `step_history[].review_score.overall` and skips non-numeric entries; (f) `wall_clock_minutes` parses ISO timestamps and returns None when either is missing.

- [x] T-2: Move the 6 computation functions verbatim from `scripts/inline/ingest-feature-metrics.py:67-314` into `config/scripts/orchestrator_next/record.py` (GREEN). FR-1 — signatures unchanged, logic byte-equivalent.
  Verify: T-1 tests pass. `python -c "from orchestrator_next.record import parse_tasks, compute_retries, compute_resolution, run_git_churn, extract_review_scores, wall_clock_minutes"` succeeds. `diff <(grep -A 20 "def parse_tasks" scripts/inline/ingest-feature-metrics.py) <(grep -A 20 "def parse_tasks" config/scripts/orchestrator_next/record.py)` shows the function bodies match (modulo surrounding context).
  depends: T-1

- [x] T-3: Write tests for `_resolve_feature_metrics` schema branching and dict shape (RED). FR-1, FR-5, AC-3, AC-4 — schema-aware NULL behavior is the trickiest branch and must be 100% covered.
  Verify: `pytest config/scripts/orchestrator_next/tests/test_feature_metrics_compute.py::test_resolve_feature_metrics -x` runs and FAILS. Tests cover: (a) `feature` schema with valid `tasks.md` returns dict with all 24 mapped keys (`schema_name`, `tasks_*`, `pass_at_*`, churn keys, review keys, `wall_clock_minutes`, `source`); (b) `spike` schema with no `tasks.md` returns dict with NULL task columns; (c) `feature` schema with missing `tasks.md` raises `FileNotFoundError`; (d) `feature` schema with missing `started_at` raises `RuntimeError`; (e) `source` field starts with `done@`; (f) function does not call `duckdb.connect` (verified by patching `duckdb.connect` to raise — function must not invoke it).
  depends: T-2

- [x] T-4: Implement `_resolve_feature_metrics(state, change_id)` in `record.py` (GREEN). FR-1, FR-5.
  Verify: T-3 tests pass. `python -c "from orchestrator_next.record import _resolve_feature_metrics; help(_resolve_feature_metrics)"` shows the documented signature.
  depends: T-3

- [x] T-5: Write tests for `_write_feature_metrics` (RED). FR-2 — calls `upsert_feature_metrics` with the right kwargs; caller controls transaction.
  Verify: `pytest config/scripts/orchestrator_next/tests/test_feature_metrics_compute.py::test_write_feature_metrics -x` FAILS. Tests cover: (a) calling the helper with a dict produces a `feature_metrics` row in DuckDB matching the dict values; (b) the helper does NOT issue BEGIN/COMMIT (caller controls transaction — verified by inspecting `db.execute` call args via mock); (c) helper raises if `upsert_feature_metrics` raises (no swallowing).
  depends: T-2

- [x] T-6: Implement `_write_feature_metrics(db, repo_root, change_id, data)` in `record.py` (GREEN). FR-2.
  Verify: T-5 tests pass.
  depends: T-5

- [x] T-7: Write tests for the `mark-change-completed` trigger and atomic ROLLBACK in `record()` (RED). FR-3, FR-4, NFR-1, AC-1, AC-2, AC-5 — atomicity is the core consistency guarantee.
  Verify: `pytest config/scripts/orchestrator_next/tests/test_feature_metrics_trigger.py -x` FAILS. Tests cover: (a) `record()` invoked with `step_id="mark-change-completed"` and `status="completed"` writes both a `step_events` row and a `feature_metrics` row; (b) `_write_feature_metrics` mocked to raise → no `step_events` row remains for that call (SELECT COUNT == 0) AND exit code is non-zero; (c) `_resolve_feature_metrics` mocked to raise → BEGIN was never issued (mock asserts `db.execute("BEGIN")` not called) AND exit code is non-zero; (d) non-`mark-change-completed` step still routes through the existing Phase 4 boundary path (regression check); (e) git-log subprocess timeout simulated → row is written with zero churn columns and exit code is 0 (FR-4's `run_git_churn` non-fatal policy is preserved through the trigger); (f) `mark-change-completed` with `status="recovered"` does NOT trigger the absorbed path (regression check — only `completed` triggers).
  depends: T-4, T-6

- [x] T-8: Wire the `mark-change-completed` trigger into `record()` in `record.py` (GREEN). FR-3, FR-4, NFR-1.
  Verify: T-7 tests pass. Manual: `pytest config/scripts/orchestrator_next/tests/ -x` (full suite) passes — the existing Phase 4 boundary tests still pass because the trigger sets `_phase5_handled = True` and the legacy path is gated on `not _phase5_handled`.
  depends: T-7

- [x] T-9: Write parity test against the `done-verb-level-aware-writes` archive fixture (RED). FR-10, AC-6 — cycle-20 shape/value parity rule.
  Verify: `pytest config/scripts/orchestrator_next/tests/test_feature_metrics_parity.py -x` runs and FAILS pre-Stage-A (legacy script writes its row but the absorbed path's row is missing or differs). The test runs both implementations against `spec/changes/archive/2026-04-25-done-verb-level-aware-writes/state.yaml` and `tasks.md` into two tmp DuckDBs, then SELECTs the `feature_metrics` row from each and asserts equal across these 24 columns: `repo_root, change_id, schema_name, tasks_total, tasks_planned, tasks_added, tasks_completed, tasks_failed, resolve_rate, pass_at_1, pass_at_2, regressions, regression_rate, retries_total, human_interventions, files_changed, insertions, deletions, total_commits, rework_commits, rework_rate, review_scores_json, review_score_avg, wall_clock_minutes`. `source` and `computed_at` are excluded.
  depends: T-8

- [x] T-10: Make the parity test pass by aligning any drift between `_resolve_feature_metrics` and the legacy script (GREEN). FR-10.
  Verify: `pytest config/scripts/orchestrator_next/tests/test_feature_metrics_parity.py -x` passes. If a column drift appears, the fix is in `_resolve_feature_metrics` (the legacy script is the byte-truth source for this cycle); document any deliberate divergence in design.md Decisions.
  depends: T-9

- [x] T-11: Stage A review checkpoint.
  Verify: All Stage A tests pass (`pytest config/scripts/orchestrator_next/tests/test_feature_metrics_*.py -x`). The full `pytest config/scripts/orchestrator_next/tests/ -x` suite passes (Phase 4 regression check). Coverage on the changed `record.py` regions ≥ 90%. The inline `ingest-feature-metrics.py` script and its `_complete-phase.yaml` step entry are still in place (Stage B has not yet run) — verified by `grep -n "ingest-feature-metrics" config/workflows/_complete-phase.yaml` returning the line and `ls scripts/inline/ingest-feature-metrics.py` succeeding. Bootstrap-safety check: `feature_metrics` table contains the row this very feature wrote during its own complete-phase run (the inline script wrote it; the absorbed path also wrote it earlier in the same complete-phase run; INSERT OR REPLACE means whichever ran second is what landed — both should produce identical 24-column values, verified by the parity test).
  depends: T-10

## Stage B — deletion (script, contract, complete-phase entry, legacy test)

- [x] T-12: Write tests for the rewritten `test-complete-phase-order.sh` and the absent-step assertion (RED). FR-7, FR-8, AC-7 — the surviving invariant is `mark-change-completed → compute-swe-metrics` plus `ingest-feature-metrics` absence.
  Verify: After editing `config/tests/test-complete-phase-order.sh` to drop `ingest-feature-metrics` from `REQUIRED_ORDER` and add the absence + `mark→swe` ordering checks, run `bash config/tests/test-complete-phase-order.sh` against the CURRENT (unedited) `_complete-phase.yaml`. The script MUST exit non-zero with a "ingest-feature-metrics is unexpectedly present" failure, proving the new invariant is enforced. Inspect: the script no longer includes `ingest-feature-metrics` in `REQUIRED_ORDER`; it asserts `ingest-feature-metrics` is absent; it asserts `mark-change-completed` precedes `compute-swe-metrics`.

- [x] T-13: Remove `- ingest-feature-metrics` from `config/workflows/_complete-phase.yaml` (GREEN). FR-7.
  Verify: `grep -n "ingest-feature-metrics" config/workflows/_complete-phase.yaml` returns no matches. `bash config/tests/test-complete-phase-order.sh` exits 0. The step list now contains 6 entries.
  depends: T-12

- [x] T-14: Delete `scripts/inline/ingest-feature-metrics.py` and `config/steps/ingest-feature-metrics.yaml`. FR-7.
  Verify: `ls scripts/inline/ingest-feature-metrics.py config/steps/ingest-feature-metrics.yaml` exits non-zero with "No such file". `grep -rn "ingest-feature-metrics" config/ scripts/ --include='*.yaml' --include='*.py' --include='*.sh' | grep -v "^config/scripts/orchestrator_next/tests/"` returns no matches (the parity test legitimately mentions the legacy path in a comment, but the test itself has been updated by T-15 to no longer subprocess-invoke the deleted script).
  depends: T-13

- [x] T-15: Update the parity test to handle the legacy script's deletion. The test must continue to assert that the absorbed path produces the same 24 columns it produced at Stage A — using a captured byte-for-byte expected-row JSON snapshot recorded during T-10 instead of re-running the now-deleted script.
  Verify: `pytest config/scripts/orchestrator_next/tests/test_feature_metrics_parity.py -x` passes. The test no longer references `scripts/inline/ingest-feature-metrics.py` (verified by `grep -n "ingest-feature-metrics" config/scripts/orchestrator_next/tests/test_feature_metrics_parity.py` returning either no matches or only a historical comment). The expected-row snapshot file (e.g., `config/scripts/orchestrator_next/tests/fixtures/feature_metrics_expected.json`) is checked in.
  depends: T-14

- [x] T-16: Delete `config/scripts/__tests__/test-ingest-feature-metrics.sh` and remove its entry from `config/scripts/verify-all.sh` (lines 107-108). FR-9.
  Verify: `ls config/scripts/__tests__/test-ingest-feature-metrics.sh` exits non-zero. `grep -n "test-ingest-feature-metrics" config/scripts/verify-all.sh` returns no matches. `bash config/scripts/verify-all.sh` exits 0.
  depends: T-13

- [x] T-17: Remove the `ingest-feature-metrics:` key from `config/scripts/__tests__/fixtures/baseline_compute_swe_metrics.yaml` (line 81). FR-9.
  Verify: `grep -n "ingest-feature-metrics" config/scripts/__tests__/fixtures/baseline_compute_swe_metrics.yaml` returns no matches. Any test consuming this fixture (`grep -rln "baseline_compute_swe_metrics" config/scripts/`) still passes — re-run the relevant tests.
  depends: T-13

- [x] T-18: Stage B end-to-end smoke. Run a complete-phase against an archived feature fixture with the absorbed path active and the inline script deleted. AC-1, AC-7, AC-8 — the complete phase must finish successfully and produce a `feature_metrics` row sourced from `done@...`.
  Verify: After running the complete phase against a fixture, `duckdb metrics.duckdb -c "SELECT source FROM feature_metrics WHERE change_id = '<fixture_id>'"` returns a single row whose `source` column starts with `done@`. `duckdb metrics.duckdb -c "SELECT * FROM feature_report WHERE change_id = '<fixture_id>'"` returns one row with non-NULL `tasks_total` (the LEFT JOIN now sees a populated `feature_metrics` row at `compute-swe-metrics` time).
  depends: T-15, T-16, T-17

- [x] T-19: Stage B review checkpoint.
  Verify: All Stage A + B tests pass (`pytest config/scripts/orchestrator_next/tests/ -x`). `bash config/scripts/verify-all.sh` exits 0. `bash config/tests/test-complete-phase-order.sh` exits 0. `grep -rn "ingest-feature-metrics" config/ scripts/ agents/ skills/ CLAUDE.md` returns no matches outside historical comments. Coverage on changed `record.py` regions ≥ 90%. Manual: re-run T-18 smoke against a second fixture for redundancy.
  depends: T-18

<!-- Status markers: [ ] pending, [→] in-progress, [x] done, [~] skipped -->
<!-- Format: per artifact-formats.md § Task Format Contract -->
<!-- TDD: test tasks (RED) always precede implementation tasks (GREEN) -->
<!-- Coverage target: >= 90% at each phase gate -->

## Fix Tasks (from implement-phase review)

- [x] FT-20: Harden `dispatch.py` to handle missing step contracts on the `run_step` path [RESOLVED in commit f658f90]. CF-1 / NFR-3 / AC-9 — dispatch.py:353 calls `load_contract_for_step` without try/except; `state.yaml.workflow_plan.complete.active` still lists `ingest-feature-metrics`; complete phase will crash.
  Verify:
  1. From worktree root `config/scripts/`: simulate dispatch with `ingest-feature-metrics` as next step and `ORCHESTRATOR_HOME=worktree` — confirm no FileNotFoundError (returns stub contract or skip action).
  2. `pytest config/scripts/orchestrator_next/tests/ -q` — 341 passed, 2 pre-existing failures unchanged.
  3. `dispatch.py:282-289` pattern should be mirrored at lines 352–357 (5-line try/except with sys.stderr warning and stub contract fallback: `agent="inline"`, `run=None`, `instruction=""`, `rules=[]`).
