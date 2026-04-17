# Tasks — duckdb-ingest-normalized-metrics-tables (HL-284)

TDD ordering: every implementation task is preceded by a RED test task that must fail before the impl task is done, and pass after.

---

## Phase 1 — DDL (register-repo.sh)

- [x] T-1: Create `config/scripts/__tests__/register-repo.test.sh` with a RED test: seed an empty `$TMPDIR` DB, run `register-repo.sh` against a minimal fixture repo (no archives), then assert `SHOW TABLES` returns exactly 4 names: `features, step_history, per_agent_metrics, per_step_metrics`.
  Verify: `bash config/scripts/__tests__/register-repo.test.sh` runs; the "schema tables exist" assertion FAILS (three tables absent).

- [x] T-2: Add `CREATE TABLE IF NOT EXISTS` DDL for `step_history`, `per_agent_metrics`, `per_step_metrics` to `register-repo.sh` (inside the existing `duckdb "$DB" <<'SQL' ... SQL` block or a sibling block immediately after). Use the exact column definitions from `design.md` (no FKs).
  Verify: `bash config/scripts/__tests__/register-repo.test.sh` → T-1's schema assertion PASSES; running `register-repo.sh` twice in a row produces no error (AC-1).

---

## Phase 2 — Child-row ingest, full-data path

- [x] T-3: Extend `register-repo.test.sh` with a `feature-full` fixture state.yaml under `$TMPDIR/fake-repo/spec/changes/archive/feature-full/state.yaml` containing `step_history[]` with 2 entries (one with full `usage`, one with agent but no `usage`), `metrics.per_agent_tokens` encoded as a **JSON-string scalar** (quoted YAML string containing JSON, matching real archives — e.g. `per_agent_tokens: '{"agent-a": {"total_tokens": 100, "cost_usd": 0.01, "tool_uses": 5, "duration_ms": 1000, "steps": 1}, "agent-b": {"total_tokens": 200, "cost_usd": 0.02, "tool_uses": 10, "duration_ms": 2000, "steps": 1}}'`) with 2 agents, `metrics.per_step` with 2 step_ids. Add RED assertions: after `register-repo.sh` ingests this fixture, `SELECT COUNT(*) FROM step_history WHERE change_id='feature-full'` returns 2, `per_agent_metrics` returns 2, `per_step_metrics` returns 2; `agent` column is correctly populated for the entry that has one.
  Verify: new assertions FAIL (tables still empty — no ingest logic yet).

- [x] T-4: Implement child-row ingest inside the feature loop in `register-repo.sh`. Order: (1) DELETE from all 3 child tables for `(repo_root, change_id)`, (2) the existing `INSERT OR REPLACE INTO features`, (3) iterate `step_history[]` indices via `yq` and emit one INSERT per entry (use `// null` guards for missing `usage` subfields), (4) iterate `.metrics.per_agent_tokens | keys` and emit one INSERT per agent, (5) iterate `.metrics.per_step | keys` and emit one INSERT per step_id. Apply `sql_quote` to every interpolated string.
  Verify: T-3 assertions PASS; row counts match exactly 2 / 2 / 2.

---

## Phase 3 — Graceful-skip paths (UC-E1, UC-E2, UC-E3)

- [x] T-5: Add RED fixtures + assertions to `register-repo.test.sh`:
  (a) `feature-partial` — step_history with one entry (has `usage`), no `metrics.per_agent_tokens` key, no `metrics.per_step` key. Assert: `step_history` count = 1, `per_agent_metrics` count = 0, `per_step_metrics` count = 0, `register-repo.sh` exit code = 0.
  (b) `feature-no-usage` — single step_history entry with NO `usage` block. Assert: `SELECT total_tokens, tool_uses, duration_ms FROM step_history WHERE change_id='feature-no-usage'` returns a single row of `NULL,NULL,NULL`.
  Verify: run the test — assertions FAIL (ingest either errors or inserts incorrect data) before Phase-3 impl is in place. (If Phase-2 impl already handles these cleanly, this task's RED step may pass accidentally; if so, document in Verify and proceed.)

- [x] T-6: Harden ingest in `register-repo.sh` to cover the graceful-skip matrix. NOTE: `metrics.per_agent_tokens` in real archives is a JSON-encoded **string scalar** (YAML `!!str`), NOT a map — guard by extracting the raw string (`yq -r '.metrics.per_agent_tokens // ""'`) and skipping when the result is empty or equals the literal `null`; when present, parse with `yq -p=json` (or pipe to `fromjson`) to iterate keys. Guard `.metrics.per_step` similarly (it IS a YAML map — check `yq '.metrics.per_step | type'` == `"!!map"`). For `usage` subfields use `// null` and emit SQL literal `NULL` (not empty string). Ensure exit 0 for all three graceful-skip cases.
  Verify: T-5 assertions PASS; no stderr from register-repo.sh on these fixtures.

---

## Phase 4 — Idempotency & rebuild ordering (AC-2, UC-2)

- [x] T-7: Add RED assertion to `register-repo.test.sh`: run `register-repo.sh` twice back-to-back against `feature-full`; assert all 4 table row counts are identical after run 1 and run 2 (idempotency).
  Verify: if Phase 2 child-first delete is correctly implemented this passes immediately; if not it fails with duplicate-row or PK-violation errors.

- [x] T-8: Add RED assertion to `register-repo.test.sh`: ingest `feature-full`, then run `register-repo.sh --rebuild`, then assert (a) all four tables' row counts for that repo_root are identical to the original ingest and (b) at no intermediate point did a foreign-key-style error occur (captured via stderr buffer).
  Verify: RED assertion FAILS if rebuild logic doesn't delete children first.

- [x] T-9: Update the `--rebuild` block in `register-repo.sh` to `DELETE FROM step_history / per_agent_metrics / per_step_metrics WHERE repo_root = '...'` BEFORE the existing `DELETE FROM features` statement. All four DELETEs go into one `duckdb <<SQL` heredoc.
  Verify: T-8 assertions PASS.

---

## Phase 5 — Named queries (metrics-query.sh)

- [x] T-10: Extend `config/scripts/metrics-query.test.sh` fixture (the existing inline DDL block at ~line 138) to also `CREATE TABLE` the three new tables using the DDL from `design.md`, and seed rows for `REPO_A/feature-alpha`: 2 `per_step_metrics` rows (e.g., `implement`/cost 0.50, `review`/cost 0.20), 3 `per_agent_metrics` rows where one agent's `duration_ms` is >2× the mean (outlier trigger). Add RED assertions: `step-cost-hotspots --repo REPO_A` exits 0 + non-empty; `agent-cost-hotspots --repo REPO_A` exits 0 + non-empty; `agent-duration-outliers --repo REPO_A` exits 0 + output contains the outlier agent name; `step-cost-hotspots --fleet` exits 0 + non-empty; `step-cost-hotspots --repo REPO_B` exits non-zero + empty (zero-row path).
  Verify: new assertions FAIL because the three query ids aren't recognized by `metrics-query.sh` (exit 2).

- [x] T-11: Add three `case` arms to `metrics-query.sh` (`step-cost-hotspots`, `agent-cost-hotspots`, `agent-duration-outliers`) using the exact SQL from `design.md`. Reuse `${SCOPE}` and `${LIMIT_CLAUSE}`. No changes to arg parsing.
  Verify: T-10 assertions PASS; running `metrics-query.sh agent-duration-outliers --repo REPO_A` returns the seeded outlier agent row.

- [x] T-12: Confirm no existing assertions broke: run the full test file and expect all previous assertions plus the new ones to pass.
  Verify: `bash config/scripts/metrics-query.test.sh` prints `Results: N passed, 0 failed` with N = original_count (27+) + new_assertion_count (≥ 6). AC-5 satisfied.

---

## Phase 6 — json_extract-free consumer check (AC-3)

- [x] T-13: Add a RED assertion to `register-repo.test.sh` (or a new small section in `metrics-query.test.sh`) that executes raw SQL via `duckdb -csv "$TEST_DB" "SELECT agent, total_tokens FROM per_agent_metrics WHERE change_id='feature-alpha' ORDER BY agent"` and asserts (a) exit 0, (b) the output contains all seeded agent names, (c) the SQL string contains no `json_extract` substring.
  Verify: assertion passes after Phase 5; demonstrates AC-3 directly.

---

## Phase 7 — Backfill & documentation (AC-6, AC-8)

- [x] T-14: Run `ORCHESTRATOR_HOME=<orchestrator-repo> bash config/scripts/register-repo.sh --rebuild <orchestrator-repo>` against the local orchestrator repo. Capture the output of `duckdb $METRICS_DB "SELECT 'features' AS t, COUNT(*) FROM features UNION ALL SELECT 'step_history', COUNT(*) FROM step_history UNION ALL SELECT 'per_agent_metrics', COUNT(*) FROM per_agent_metrics UNION ALL SELECT 'per_step_metrics', COUNT(*) FROM per_step_metrics"`. Record counts in `verify.md` with a note that `per_step_metrics = 0` is expected until `feature/metrics-capture-and-workflow-streamlining` merges.
  Verify: `verify.md` contains a "Backfill row counts" section with the four counts and the expected-zero caveat. AC-6 satisfied.

- [x] T-15: Append a "Manual deletion contract" section to `design.md` (if not already present) stating: `DELETE FROM features WHERE ...` outside `register-repo.sh` leaves child orphan rows; operators must use `register-repo.sh --rebuild` to delete. Verify by running `duckdb $METRICS_DB "DELETE FROM features WHERE repo_root='/tmp/fake' AND change_id='feature-full'"` after an ingest and confirming child tables still hold the orphan rows (documented, not a test failure).
  Verify: `design.md` includes the section; an ad-hoc shell check shows orphan rows survive a direct features delete. AC-8 satisfied.

---

## Phase 8 — Regression sweep

- [x] T-16: Run both test scripts end-to-end: `bash config/scripts/metrics-query.test.sh && bash config/scripts/__tests__/register-repo.test.sh`. Ensure zero failures in each.
  Verify: both scripts print a `Results: N passed, 0 failed` summary and exit 0.
