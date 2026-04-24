# Tasks: Report views retire CLI — Phase 3 of workflow-engine-as-state-machine

- [x] T-0 Validation smoke: verify DuckDB compiles the `feature_report` DDL sketch
  - **Why**: FR-1, FR-2; cycle-12 rule — validate SQL sketches against live schema before committing
  - **Files**:
    - (read-only) `config/scripts/orchestrator_next/upsert.py`
    - (read-only) `config/scripts/orchestrator_next/migrations/0001_seed_pricing.sql`
  - **Approach**: Spin up an in-memory DuckDB via the `in_memory_db` fixture, run `ensure_schema(db)`, then execute the `CREATE OR REPLACE VIEW feature_report AS ...` SQL from design.md verbatim and assert it compiles. Does not land a committed migration; validates the design's SQL before T-1 is written.
  - **Verify**: `python -c "import duckdb; db=duckdb.connect(':memory:'); …"` prints no error. If DuckDB rejects any CTE shape, the exact reformulation is recorded as a T-0 note and applied in T-2.

- [x] T-1 Write tests: view DDL shape + column coverage (RED — tests must fail)
  - **Why**: FR-1, FR-2, FR-3, FR-4, FR-5, AC-1, AC-2, AC-3, AC-11
  - **Files**:
    - `config/scripts/orchestrator_next/tests/test_report_views.py` (create)
    - (read-only) `config/scripts/orchestrator_next/tests/test_reconcile_in_progress.py` (for the `in_memory_db` fixture pattern)
  - **Approach**: Write pytest cases asserting: (a) `DESCRIBE feature_report` returns the exact column list from FR-2; (b) one-row-per-change with seeded fixtures; (c) `COALESCE(SUM(cost_usd),0)` handles NULL (UC-E1); (d) LEFT JOIN keeps changes whose `feature_metrics` row is missing (UC-E2); (e) every zero-division guard fires when denom=0; (f) `per_agent_tokens` / `per_step` are strings parseable by `json.loads`, AND when 2+ distinct agents are seeded for the same change_id, `json.loads(per_agent_tokens)` returns a dict with 2+ top-level keys (one per agent) — NOT one row per agent. Plus DESCRIBE assertions for `phase_report`, `agent_report`, `repo_report`.
  - **Verify**: `pytest config/scripts/orchestrator_next/tests/test_report_views.py -v` — all tests FAIL with "View with name feature_report does not exist" (red) for the right reason.

- [x] T-2 Implement: `0002_report_views.sql` migration (GREEN) (depends: T-1)
  - **Why**: FR-1, FR-2, FR-3, FR-4, FR-5, DV-1, DV-2, DV-3, DV-4, DV-7
  - **Files**:
    - `config/scripts/orchestrator_next/migrations/0002_report_views.sql` (create)
  - **Approach**: Write the four `CREATE OR REPLACE VIEW` statements exactly as sketched in design.md § Components §1. Migration auto-discovered and applied by `_run_migrations`. No Python changes in this task.
  - **Verify**: `pytest config/scripts/orchestrator_next/tests/test_report_views.py -v` — all T-1 tests pass (green). `pytest config/scripts/orchestrator_next/tests/test_migrations.py` still passes (schema_migrations now shows both `0001_*` and `0002_*`).

- [x] T-3 Write tests: byte-equivalence baseline capture — construct fixtures (RED) (depends: T-2)
  - **Why**: NFR-1, AC-4, AC-5, AC-6, DV-5, D-4, D-5
  - **Files**:
    - `config/scripts/__tests__/fixtures/baseline.duckdb.sql` (create — deterministic SQL dump)
    - `config/scripts/__tests__/fixtures/baseline_compute_swe_metrics.yaml` (create — frozen bytes)
    - `config/scripts/__tests__/fixtures/baseline_read_sub_state_metrics.yaml` (create — frozen bytes)
    - (read-only) `spec/changes/archive/2026-04-21-durable-intent-and-resume/state.yaml`
    - (read-only) `scripts/inline/compute-swe-metrics.sh` (pre-rewrite — captured via git blob at this task's execution time)
    - (read-only) `config/scripts/read-sub-state-metrics.sh` (pre-rewrite)
  - **Approach**: Write a one-shot fixture-build script (in this task's commit message / comment block — not checked in as a runnable script) that (1) opens a fresh DuckDB, calls `ensure_schema`, (2) replays every completed step_history entry from the archived state.yaml through `upsert_step_event`, (3) dumps the DB via `duckdb -c ".dump"` to `baseline.duckdb.sql`, (4) runs the pre-rewrite `compute-swe-metrics.sh` and `read-sub-state-metrics.sh` against the DB and redirects stdout to the two `.yaml` fixtures. Commit all three fixtures. Tests in T-5 / T-7 will diff against them.
  - **Verify**: Three fixture files exist at committed paths. `duckdb :memory: < baseline.duckdb.sql` loads without error. Both `.yaml` fixtures parse via `yaml.safe_load` and have `metrics:` top-level key. No test file executes yet — this is pure fixture capture.

- [x] T-4 Write tests: byte-equivalence test for `compute-swe-metrics.sh` (RED) (depends: T-3)
  - **Why**: NFR-1, AC-4, AC-5, FR-6
  - **Files**:
    - `config/scripts/__tests__/compute-swe-metrics-projection.test.sh` (rewrite)
  - **Approach**: Replace current RED body with: load `baseline.duckdb.sql` into a temp DB, run the current (not-yet-rewritten) `scripts/inline/compute-swe-metrics.sh` with a temp state.yaml pointing to the baseline change_id, `diff` stdout against `baseline_compute_swe_metrics.yaml`. Run twice for repeatability check (UC-E3). Expected state: **fails** because current script calls `orchestrator metrics` (which still works pre-T-8), so actually may pass — the TRUE red is exercised after T-8 when the verb is removed. Task body must make explicit: RED here asserts the diff-after-two-runs is empty (determinism); the full RED bite fires at T-5.
  - **Verify**: Shell script exits 0 (two successive runs byte-identical); diff against `baseline_compute_swe_metrics.yaml` is empty. This is the parity contract that T-5 must preserve.

- [x] T-5 Implement: rewrite `compute-swe-metrics.sh` to use `feature_report` (GREEN) (depends: T-4)
  - **Why**: FR-6, FR-9, NFR-1, AC-5, UC-2
  - **Files**:
    - `scripts/inline/compute-swe-metrics.sh` (rewrite)
  - **Approach**: Replace the `orchestrator metrics --format json` shell-out with `duckdb -readonly -json -c "SELECT * FROM feature_report WHERE change_id = '$CHANGE_ID'"` + the `python3 -c` reshape block from design.md § Components §3. Add the slug-guard check before embedding `$CHANGE_ID` in SQL. Preserve bash 3.2 compatibility.
  - **Verify**: `bash config/scripts/__tests__/compute-swe-metrics-projection.test.sh` passes (green). `diff <(bash scripts/inline/compute-swe-metrics.sh <tmp_state_dir>) config/scripts/__tests__/fixtures/baseline_compute_swe_metrics.yaml` is empty.

- [x] T-6 Write tests: byte-equivalence test for `read-sub-state-metrics.sh` (RED) (depends: T-5)
  - **Why**: NFR-1, AC-6, FR-9, UC-3, D-4
  - **Files**:
    - `config/scripts/__tests__/read-sub-state-metrics.test.sh` (rewrite)
  - **Approach**: Seed DB from `baseline.duckdb.sql`, invoke `read-sub-state-metrics.sh` with the baseline slug, diff stdout against `baseline_read_sub_state_metrics.yaml`. Assert exactly three top-level keys under `metrics:`: `tokens.total`, `duration_ms`, `churn.files_changed`. No extraneous keys.
  - **Verify**: Test fails initially because the current `read-sub-state-metrics.sh` shells out to `orchestrator metrics` — run in isolation it should still pass pre-T-8, but the RED-for-right-reason emerges when T-8 deletes the verb. Document this timing in the test comment.

- [x] T-7 Implement: rewrite `read-sub-state-metrics.sh` to use `feature_report` (GREEN) (depends: T-6)
  - **Why**: FR-6, FR-9, NFR-1, AC-6, UC-3, D-4
  - **Files**:
    - `config/scripts/read-sub-state-metrics.sh` (rewrite)
  - **Approach**: Replace `orchestrator metrics --format json` with `duckdb -readonly -json -c "SELECT total_tokens, duration_ms, files_changed FROM feature_report WHERE change_id = '$SLUG'"` + narrow `python3 -c` block from design.md § Components §4. Add slug-guard.
  - **Verify**: `bash config/scripts/__tests__/read-sub-state-metrics.test.sh` passes. Diff against the baseline fixture is empty.

- [x] T-8 Write tests: `scripts/cost-report.sh` + SKILL.md integration (RED) (depends: T-7)
  - **Why**: FR-8, AC-10, UC-1
  - **Files**:
    - `config/scripts/__tests__/cost-report.test.sh` (create)
  - **Approach**: Seed DB from `baseline.duckdb.sql`. Test cases: (a) `scripts/cost-report.sh --change-id $BASELINE_CID` exits 0 and stdout contains all 8 section headers (`## Executive Summary`, `## Per-Phase`, `## Per-Agent`, `## Per-Model`, `## Native Tools`, `## MCP Calls`, `## Per-Agent Tool Use`, `## Anomalies`); (b) slug-guard rejection returns exit 3; (c) unknown change_id returns exit 1 with `no events` stderr; (d) repeated runs byte-identical; (e) presence check: `grep -c '^| Total cost |'` == 1 in the Exec Summary table.
  - **Verify**: Tests fail — `scripts/cost-report.sh` does not yet exist.

- [x] T-9 Implement: `scripts/cost-report.sh` + decision gate on `render_markdown_feature` (GREEN) (depends: T-8)
  - **Why**: FR-8, AC-10, DV-6, UC-1, D-2
  - **Files**:
    - `scripts/cost-report.sh` (create)
    - `skills/orchestrate/SKILL.md` (edit — lines 97–102 to replace `orchestrator cost` invocation)
    - (conditional, see Decision gate) `config/scripts/orchestrator_next/cost_report.py` (retain `render_markdown_feature` + helpers if gate decides)
  - **Approach**: Implement the shell wrapper per design.md § Components §2. Two `duckdb -readonly -json` queries (feature_report row + per-model GROUP BY), `python3 -c` inline formatter producing 8 sections. Update SKILL.md: replace `cost_report = run \`orchestrator cost --change-id $CHANGE_ID\`` with `cost_report = run \`scripts/cost-report.sh --change-id $CHANGE_ID\``. **Decision gate**: run `diff <(scripts/cost-report.sh --change-id $BASELINE_CID) <(python -c "from orchestrator_next.cost_report import render_markdown_feature, aggregate_feature; import duckdb, json; db=duckdb.connect('baseline.duckdb'); data=aggregate_feature(db,'$BASELINE_REPO','$BASELINE_CID'); print(render_markdown_feature(data), end='')")`. If diff is empty → delete `render_markdown_feature` and its helpers in T-12. If non-empty → preserve them; the inline `python3 -c` replaces its body with `from orchestrator_next.cost_report import render_markdown_feature; print(render_markdown_feature(data), end='')`. Record decision inline as a comment at the top of `scripts/cost-report.sh`.
  - **Verify**: `bash config/scripts/__tests__/cost-report.test.sh` passes (green). Updated SKILL.md line reads `scripts/cost-report.sh`. Decision gate outcome recorded in script comment.

- [x] T-10 Write tests: retired-CLI regression — verbs return exit 3 (RED) (depends: T-9)
  - **Why**: FR-7, NFR-4, AC-7, AC-8
  - **Files**:
    - `config/scripts/orchestrator_next/tests/test_retired_cli.py` (create)
  - **Approach**: pytest subprocess tests: (a) `bin/orchestrator cost --change-id foo` exits 3, stderr contains "Usage:" and does not contain "cost"; (b) `bin/orchestrator metrics --change-id foo` exits 3 similarly; (c) grep assertion: running `rg -l 'orchestrator (cost|metrics)' bin/ config/scripts/ scripts/ skills/ --glob '!**/archive/**' --glob '!**/.state/**' --glob '!**/backlog.md'` returns zero matches.
  - **Verify**: Tests fail — current main() accepts `cost` and `metrics`; grep currently finds matches in SKILL.md (already fixed in T-9) and potentially test files (addressed in T-11/T-12).

- [x] T-11 Implement: delete `_metrics_main`, `_cost_main`, update `_usage` + `main()` (GREEN) (depends: T-10)
  - **Why**: FR-7, NFR-4, AC-7, AC-8, DV-7
  - **Files**:
    - `bin/orchestrator` (edit — delete lines 56–146 `_metrics_main`, lines 149–284 `_cost_main`; remove `metrics` / `cost` usage lines from `_usage`; remove `"cost"` / `"metrics"` from the verb tuple at line 568 and the branches at lines 573–579)
    - `config/tests/test-orchestrator-metrics-json-shape.sh` (delete)
    - `config/tests/test-metrics-pipeline-integration.sh` (audit: if it references `orchestrator metrics`, delete; if not, leave untouched — task notes the decision)
  - **Approach**: Surgical deletion of the two handler functions and their dispatch entries. Update `_usage()` to omit the two retired verbs. Audit `test-metrics-pipeline-integration.sh` for references; delete if it subprocesses either retired verb.
  - **Verify**: `pytest config/scripts/orchestrator_next/tests/test_retired_cli.py` passes. `python bin/orchestrator cost --change-id foo` exits 3. The grep assertion (AC-7) returns zero matches across production directories.

- [x] T-12 Implement: delete `metrics_report.py`, trim `cost_report.py`, delete `test_cost_cli.py` (GREEN) (depends: T-11)
  - **Why**: FR-11, FR-10 (partial)
  - **Files**:
    - `config/scripts/orchestrator_next/metrics_report.py` (delete)
    - `config/scripts/orchestrator_next/cost_report.py` (trim to `_anomalies`, `_step_allowlist_anomalies`, imports, docstring — plus `render_markdown_feature` + `_md_table` + `_fmt_usd` + `_fmt_tokens` + `_fmt_ms` iff T-9 decision gate kept them)
    - `config/scripts/tests/test_cost_cli.py` (delete)
  - **Approach**: Remove the Python projection layer per FR-11 and design.md § Components §6,7,8. Preserve only the anomaly helpers and (conditionally) `render_markdown_feature`. Verify no orphan imports remain by grepping `from orchestrator_next.cost_report import` and `from orchestrator_next.metrics_report import` across the worktree.
  - **Verify**: `pytest config/scripts/orchestrator_next/tests/test_cost_report_anomaly.py` still passes (anomaly helpers intact). `pytest config/scripts/orchestrator_next/tests/` collects without ImportError. `rg 'from orchestrator_next.metrics_report' config/ bin/ scripts/ skills/` returns zero matches. `rg 'from orchestrator_next.cost_report import' config/ bin/ scripts/ skills/` returns matches only for `_anomalies` / `_step_allowlist_anomalies` (and `render_markdown_feature` if gate retained it).

- [x] T-13 Review checkpoint: phase gate — full suite + grep assertion + perf check
  - **Why**: NFR-3, NFR-5, AC-7, AC-12
  - **Files**:
    - (read-only — verification only) `config/scripts/orchestrator_next/tests/`, `config/scripts/__tests__/`, `config/scripts/tests/`
  - **Approach**: Run full pytest suite + shell test suite. Run the production-shape perf check: build a representative DB by replaying all `spec/changes/archive/*/state.yaml` step_history entries, time `scripts/cost-report.sh --change-id <large-feature-cid>` and compare against the pre-phase `orchestrator cost --change-id` timing captured before this task (use `git stash` / `git worktree` to revert and re-time, OR capture the pre-phase timing during T-0 as a documented baseline). Assert within 2× wall-clock.
  - **Verify**: All tests pass. Coverage ≥ 90% on files listed in design.md § File-Modification Table (measured via `pytest --cov=config/scripts/orchestrator_next --cov=scripts/inline --cov=scripts --cov-report=term-missing`). `rg 'orchestrator (cost|metrics)' bin/ config/ scripts/ skills/ --glob '!**/archive/**' --glob '!**/.state/**' --glob '!**/backlog.md' --glob '!**/migrations/**'` returns zero. Perf check: `scripts/cost-report.sh` < 2× wall-clock of pre-phase `orchestrator cost`.

- [x] T-15 Post-hoc: apply `0002_report_views.sql` to production `metrics.duckdb`
  - **Why**: Prod db was queried during the in-flight phase review before this feature merged to main. `ensure_schema()` auto-applies migrations on every write path, so future writes will pick up `0002` automatically post-merge — but querying views before merge requires a one-shot manual apply. Discovered during phase-3 review; see memory obs 15730.
  - **Files**:
    - (runtime state only — no code changes) `/Users/spidey/code/orchestrator/metrics.duckdb`
    - (backup) `/Users/spidey/code/orchestrator/metrics.duckdb.bak-pre-0002`
  - **Approach**: Copy prod db to `metrics.duckdb.bak-pre-0002`. Open prod db and invoke `orchestrator_next.upsert._run_migrations(db)` from this worktree, which discovers and applies `0002_report_views.sql` idempotently and records it in `schema_migrations`.
  - **Verify**: `SELECT name FROM schema_migrations` on prod db returns both `0001_seed_pricing.sql` and `0002_report_views.sql`. `duckdb_views()` lists `feature_report`, `phase_report`, `agent_report`, `repo_report`. End-to-end: `bash config/scripts/read-sub-state-metrics.sh pricing-table-in-duckdb`, `bash scripts/cost-report.sh --change-id pricing-table-in-duckdb`, and `bash scripts/inline/compute-swe-metrics.sh spec/changes/archive/2026-04-20-pricing-table-in-duckdb` all exit 0 against prod db. All three verified 2026-04-25.

- [x] T-14 Regression smoke: end-to-end workflow-complete simulation
  - **Why**: AC-1..AC-12 integration; final sanity check
  - **Files**:
    - (read-only) the entire worktree at post-T-13 state
  - **Approach**: Simulate a workflow-complete invocation by (a) running `bin/orchestrator next <state.yaml>` on this feature's own state, (b) running `scripts/cost-report.sh --change-id report-views-retire-cli` against the live `$METRICS_DB`, (c) running `bash scripts/inline/compute-swe-metrics.sh .state/report-views-retire-cli/` and verifying its YAML parses and has the expected `metrics:` top-level structure, (d) running `bash config/scripts/read-sub-state-metrics.sh report-views-retire-cli` and verifying three-key output. No new assertions or fixtures; purely an integration smoke.
  - **Verify**: All four commands exit 0. Outputs parse as YAML/markdown as expected. No `orchestrator cost` / `orchestrator metrics` references surface in any emitted output.

<!-- Status markers: [ ] pending, [→] in-progress, [x] done, [~] skipped -->
<!-- (depends: T-xxx) = dependency -->
<!-- TDD: test tasks (RED) always precede implementation tasks (GREEN) -->
<!-- Coverage target: >= 90% at each phase gate -->

<!-- VERIFICATION BUGS: If verification reveals new issues, add them as tasks -->
<!-- before proceeding. Do NOT skip ahead. -->
