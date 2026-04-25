---
feature-id: cleanup-and-delete
linear-ticket: N/A
---

# Discovery Brief: Absorb ingest-feature-metrics into `done` (Phase 5 of workflow-engine-as-state-machine)

## Feature Summary

Phase 5 absorbs `scripts/inline/ingest-feature-metrics.py` — the last standalone inline metrics script — into the `done` verb's write path inside `record.py`, so the `feature_metrics` table row is written atomically with the other boundary writes. The current script runs as a separate `_complete-phase.yaml` step, which makes the write optional and visible as a distinct CLI invocation. After absorption, `feature_metrics` is populated by `done` just like `phase_events` and `driver_sessions` were absorbed in Phase 4. The step contract, step entry in `_complete-phase.yaml`, and the standalone script are deleted; `test-complete-phase-order.sh` is rewritten to assert the surviving ordering invariant. The prior Phase 5 backlog scope (`ingest-driver`/`ingest-subagents` CLI deletion, `metrics_report.py` deletion, `cost_report.py` trimming) is already shipped in Phases 3 and 4.

## Personas & Actors

- Workflow driver (orchestrate/autopilot skill): calls `orchestrator done` at step completion; expects `feature_metrics` to be populated automatically at the `mark-change-completed` boundary.
- `compute-swe-metrics.sh` (downstream consumer): queries the `feature_report` view after `ingest-feature-metrics` in the current ordering; must still receive a populated `feature_metrics` row.
- Developer adding new features: should be able to complete a workflow without knowing `ingest-feature-metrics.py` exists.
- CI/test suite: `test-ingest-feature-metrics.sh`, `test-complete-phase-order.sh`, `verify-all.sh` must all pass or be correctly rewritten.

## Use Cases

### Happy Path

UC-1: Successful feature completion — the workflow driver calls `orchestrator done` with `status: completed` and the payload for `mark-change-completed`, so that `feature_metrics` is written atomically and `compute-swe-metrics` sees populated data in `feature_report` without any separate ingest step.

UC-2: Feature boundary write with git churn — the workflow driver completes `mark-change-completed` for a feature with many commits, so that `done` executes git log/diff churn computation inside `_resolve_feature_metrics()` and writes the complete row (tasks, churn, review scores, wall clock) without the driver invoking any script manually.

UC-3: Spike or autopilot schema (no tasks.md) — the workflow driver completes `mark-change-completed` for a spike schema without a `tasks.md`, so that `done` writes the `feature_metrics` row with NULL task columns (matching current fail-soft path in `ingest-feature-metrics.py` for non-feature/bugfix schemas) rather than hard-failing.

### Error & Edge Cases

UC-E1: Missing tasks.md for feature/bugfix schema — when `done` is called for a feature-schema workflow and no `tasks.md` is found at `tasks_path` or the `.state/<slug>/tasks.md` fallback, then `done` exits non-zero (fail loud, matching the existing inline script behavior for mandatory schemas).

UC-E2: DuckDB write failure — when the DuckDB `INSERT OR REPLACE` for `feature_metrics` fails (lock, schema mismatch), then `done` exits non-zero and the caller can observe the error; partial writes are not committed (matching the NFR-2 atomicity guarantee from Phase 4).

UC-E3: Self-bootstrapping — when the `cleanup-and-delete` feature's own complete phase runs `done` for `mark-change-completed`, but the `ingest-feature-metrics` step no longer exists in `_complete-phase.yaml`, then the workflow completes successfully because the deletion happens before or concurrently with the absorption landing.

UC-E4: Git churn failure — when the `git log` subprocess times out or returns non-zero, then `done` writes the `feature_metrics` row with zero churn columns rather than aborting (matching the non-fatal failure policy in the current `run_git_churn` function).

## Scope

### In Scope

- New helper pair `_resolve_feature_metrics(state, change_id)` and `_write_feature_metrics(db, repo_root, change_id, data)` in `record.py`, following the Phase 4 pattern of `_resolve_driver_session` / `_write_driver_session`.
- Trigger logic inside `done`: `_resolve_feature_metrics` fires when `step_id == "mark-change-completed"` and `status == "completed"` (not at the generic feature boundary — see Critical Constraint below).
- Delete `scripts/inline/ingest-feature-metrics.py`, `config/steps/ingest-feature-metrics.yaml`, and the `ingest-feature-metrics` line from `config/workflows/_complete-phase.yaml`.
- Rewrite `config/tests/test-complete-phase-order.sh`: remove `ingest-feature-metrics` assertions, add assertion that `mark-change-completed` still precedes `compute-swe-metrics`.
- Delete `config/scripts/__tests__/test-ingest-feature-metrics.sh` (legacy script test).
- Update `config/scripts/verify-all.sh` to remove the `test-ingest-feature-metrics.sh` entry.
- New TDD test: `config/scripts/orchestrator_next/tests/test_feature_metrics_boundary.py` — parity test that runs both legacy script and new helper against an archived state fixture and diffs column values.
- Update `config/scripts/__tests__/fixtures/baseline_compute_swe_metrics.yaml`: remove `ingest-feature-metrics` from the step list fixture.

### Out of Scope

- Migrating historical `feature_metrics` rows (write is INSERT OR REPLACE; idempotent going forward).
- Renaming `record.py` to `done.py` (deferred from Phase 4).
- Any changes to `upsert_feature_metrics` in `upsert.py` — the existing function is the correct write target.
- Changes to `compute-swe-metrics.sh` or `feature_report` view SQL — they are already wired correctly.
- Deleting `metrics_report.py` or `cost_report.py` — confirmed shipped in Phase 3; not present as standalone CLI.
- Deleting `orchestrator ingest-driver` / `orchestrator ingest-subagents` CLI verbs — confirmed deleted in Phase 4.
- Changing the `feature_metrics` DDL or adding new columns.

## UI Direction

N/A — no UI components.

## Key Decisions

- Selected approach: **Approach A — Mirror Phase 4 helpers** (S complexity, numeric 2). Auto-selection heuristic: lowest numeric complexity among goal-aligned approaches. Approach C (XS-tied wrapper) is goal-disqualified — keeps the standalone step alive, contradicts FR-7. Approach B (extend `_resolve_driver_session`) is correctness-disqualified — fires at `remove-worktree` (position 7) but `compute-swe-metrics` (position 5) reads `feature_metrics` via LEFT JOIN; reordering `compute-swe-metrics` is impossible because it runs git churn against the worktree that `remove-worktree` deletes.
- OQ-1 resolved with Option A: special-case `step_id == "mark-change-completed"` inside `record()`. Single one-step trigger; no new step-contract metadata field needed.
- OQ-2 resolved fatal: matches Phase 4 NFR-3 boundary-write semantics. Once the write is inside `done`'s transaction, atomicity > availability.
- OQ-3 resolved: `_resolve_feature_metrics` runs OUTSIDE BEGIN; `BEGIN; upsert_step_event(...); _write_feature_metrics(...); COMMIT;` — single transaction, resolve outside (mirrors Phase 4 subagent pattern for short tx window).
- OQ-4 resolved: `test-complete-phase-order.sh` keeps the `mark-change-completed → compute-swe-metrics` invariant and adds an `ingest-feature-metrics` absence assertion. Not deleted — ordering is meaningful even though the YAML structure is the source of truth, because future edits could regress.
- OQ-5 resolved: delete-last (Stage A absorption + parity test → Stage B deletion). Mirrors Phase 4 retro precedent. The in-flight workflow's own complete phase keeps the inline script working through Stage A.
- OQ-6 resolved: parity fixture is `spec/changes/archive/2026-04-25-done-verb-level-aware-writes/`. Diff 24 non-audit columns; exclude `source` (legitimately differs by FR-6) and `computed_at` (audit timestamp).
- DDL: no migration. `feature_metrics` already has all 25 columns (verified via `DESCRIBE feature_metrics` against live DB; cycle-12 rule).

## Open Questions

- OQ-1: Trigger semantics — where does `_write_feature_metrics` fire? Option A: special-case `step_id == "mark-change-completed"` inside `record()` (avoids touching boundary detection). Option B: introduce a step contract metadata field `triggers_feature_metrics: true` and read it in `record()` (more general but more machinery). Option C: fire at the true feature boundary (`remove-worktree`) and reorder `compute-swe-metrics` to run after `remove-worktree` — rejected because `compute-swe-metrics` uses git churn against the worktree that `remove-worktree` will have deleted. Architect picks between A and B; C is rejected.

- OQ-2: Failure mode for `_write_feature_metrics` — should it be fatal (Phase 4 semantics for boundary writes) or fail-loud-then-continue (current `ingest-feature-metrics.py` exits non-zero but is a separate step, so the workflow can retry). If fatal, the transaction must include the `feature_metrics` insert. If fail-loud-then-continue, the `feature_metrics` write is isolated from the step_events transaction. Architect picks based on desired consistency guarantee.

- OQ-3: Transaction scope — if `_write_feature_metrics` fires at `mark-change-completed` completion, it fires at a non-boundary step (not the last step of the phase). The Phase 4 transaction is phase-boundary-scoped. The `feature_metrics` write either needs its own mini-transaction or must be appended to the step_events write for `mark-change-completed`. Architect decides the transaction boundary for this write.

- OQ-4: `test-complete-phase-order.sh` rewrite target invariant — after deletion, the test should assert: `mark-change-completed` precedes `compute-swe-metrics`. It should NOT assert that `ingest-feature-metrics` is present. Confirm that this is the surviving invariant worth encoding, or whether the test should be deleted entirely (the ordering is enforced by the YAML file structure, not runtime logic).

- OQ-5: Bootstrap hazard staging — should `ingest-feature-metrics` be deleted in the first task (deletion THEN absorption, protected by the inline script still existing in the worktree via git) or as the last task (absorption lands first, then deletion)? Phase 4 used last-task deletion to ensure the in-flight workflow can still record. Recommend same pattern here.

- OQ-6: Parity test fixture — the `done-verb-level-aware-writes` archive at `spec/changes/archive/2026-04-25-done-verb-level-aware-writes/` has `state.yaml` and `tasks.md`. Confirm the parity test can use this fixture by running both legacy script and new helper against it and diffing all 25 columns. The `source` column will differ by design (different caller strings); exclude it from parity check.

---

## Technical Context

### Caller Inventory (complete)

| Location | Type | Reference |
|----------|------|-----------|
| `config/workflows/_complete-phase.yaml:20` | Step entry | `- ingest-feature-metrics` |
| `config/steps/ingest-feature-metrics.yaml` | Step contract | full definition, `inline: true`, `run: scripts/inline/ingest-feature-metrics.py` |
| `scripts/inline/ingest-feature-metrics.py` | Implementation | 440-line Python script |
| `config/tests/test-complete-phase-order.sh:6,10,12,59,87,97,99,102,105,108` | Test | ordering assertions against `_complete-phase.yaml` |
| `config/scripts/__tests__/test-ingest-feature-metrics.sh` | Test | script-level integration test with fixtures |
| `config/scripts/verify-all.sh:107-108` | Test runner | includes `test-ingest-feature-metrics.sh` |
| `config/scripts/__tests__/fixtures/baseline_compute_swe_metrics.yaml:81` | Test fixture | `ingest-feature-metrics:` key in step list |

### What `ingest-feature-metrics.py` Writes (feature_metrics columns)

All 25 columns in the INSERT OR REPLACE, sourced as follows:

| Column | Source | Failure mode |
|--------|--------|-------------|
| `repo_root`, `change_id`, `schema_name` | state.yaml | Fatal if `change_id` missing |
| `tasks_total`, `tasks_planned`, `tasks_added`, `tasks_completed`, `tasks_failed`, `resolve_rate` | `parse_tasks(tasks_md)` reading `tasks.md` | Fatal on missing file for feature/bugfix schemas |
| `pass_at_1`, `pass_at_2`, `regressions`, `regression_rate` | `compute_resolution()` from task counts + `step_history` | Returns all-None if tasks_total is None/zero |
| `retries_total`, `human_interventions` | `compute_retries()` from `state.retries` dict + `state.human_interventions` | Returns zeros if absent |
| `files_changed`, `insertions`, `deletions`, `total_commits`, `rework_commits`, `rework_rate` | `run_git_churn()` — `git log --grep change_id` then `git diff --numstat` | Non-fatal; returns zeros on any git failure |
| `review_scores_json`, `review_score_avg` | `extract_review_scores()` from `step_history[].review_score.overall` | None if no scores found |
| `wall_clock_minutes` | `wall_clock_minutes()` from `state.started_at` / `state.completed_at` | Fatal on missing timestamps |
| `source` | hardcoded `"ingest-feature-metrics@<utcnow>"` | Caller string changes post-absorption |

### Phase 4 Helper Pattern (reference for absorption shape)

`record.py` Phase 4 helpers:
- `_resolve_driver_session(state, change_id, db)` — pure compute, run outside transaction
- `_write_driver_session(db, repo_root, change_id, session)` — DB write, run inside transaction
- `_resolve_subagent_rows(repo_root, change_id, session_id)` — pure compute, run outside transaction
- `_write_subagent_events(db, repo_root, change_id, rows)` — DB writes, run inside transaction

Recommended shape for this feature:
- `_resolve_feature_metrics(state, change_id)` — calls `parse_tasks`, `compute_retries`, `compute_resolution`, `run_git_churn`, `extract_review_scores`, `wall_clock_minutes`; returns a dict; run outside transaction
- `_write_feature_metrics(db, repo_root, change_id, data)` — calls `upsert_feature_metrics(db, ...)` from `upsert.py`; run inside (or as its own mini) transaction

Note: the six computation functions (`parse_tasks`, `compute_retries`, `compute_resolution`, `run_git_churn`, `extract_review_scores`, `wall_clock_minutes`) can be moved wholesale from the inline script into `record.py` with no changes to their signatures or logic.

### Critical Constraint: Trigger Point Is Not the Generic Feature Boundary

`remove-worktree` is the last step of the last phase (`_complete-phase.yaml`). Phase 4's feature boundary fires at `remove-worktree` completion. But `feature_metrics` must be written BEFORE `compute-swe-metrics` runs (`compute-swe-metrics` reads `feature_report`, which LEFT JOINs `feature_metrics` — NULL row produces NULL output, not an error, but semantically wrong). `compute-swe-metrics` runs at position 5 in the complete phase; `remove-worktree` is position 7. Therefore `_write_feature_metrics` CANNOT fire at the generic `remove-worktree` boundary — it must fire at `mark-change-completed` completion (position 3), which already requires `completed_at` to be set (the existing step contract rule).

Confirmed via: `grep -n "feature_metrics" /Users/spidey/code/orchestrator/config/scripts/orchestrator_next/migrations/0002_report_views.sql` — `feature_report` view has `LEFT JOIN feature_metrics fm`.

### Self-Bootstrapping Hazard

Phase 4 deleted `ingest-driver-auto` and `ingest-subagents-auto` in the last tasks (T-25, T-26). The same pattern applies here:
- **Option 1 (delete-last)**: absorb `_resolve_feature_metrics`/`_write_feature_metrics` into `record.py` first; remove `ingest-feature-metrics` from `_complete-phase.yaml` and delete the script as the final tasks. In-flight workflows (including this feature's own complete phase) still run the standalone script until the deletion lands.
- **Option 2 (no-op shim)**: replace `ingest-feature-metrics.py` contents with a script that exits 0 immediately; keep the step in `_complete-phase.yaml`; clean up the shim in a follow-up. More graceful but leaves dead code behind.

Phase 4 retro confirms delete-last worked cleanly. Recommend same approach.

### Parity Test Plan

Per cycle-20 rule (shape/value parity against at least one real payload from prior implementation):

- **Fixture path**: `spec/changes/archive/2026-04-25-done-verb-level-aware-writes/state.yaml` + `spec/changes/archive/2026-04-25-done-verb-level-aware-writes/tasks.md`
- **Test file**: `config/scripts/orchestrator_next/tests/test_feature_metrics_parity.py` (new)
- **Test method**: run `ingest-feature-metrics.py` against the fixture into a temp DuckDB; then call `_resolve_feature_metrics()` + `_write_feature_metrics()` against the same fixture state dict; compare all non-audit columns column-by-column
- **Columns to compare** (24 of 25 — exclude `source`): `schema_name`, `tasks_total`, `tasks_planned`, `tasks_added`, `tasks_completed`, `tasks_failed`, `resolve_rate`, `pass_at_1`, `pass_at_2`, `regressions`, `regression_rate`, `retries_total`, `human_interventions`, `files_changed`, `insertions`, `deletions`, `total_commits`, `rework_commits`, `rework_rate`, `review_scores_json`, `review_score_avg`, `wall_clock_minutes`
- **Must fail before absorption** (RED phase): the new helpers don't exist yet so `_resolve_feature_metrics` import fails
- **Must pass after absorption** (GREEN phase): column values match the legacy script's output for the same fixture

### Relevant Files

| File | Role in this feature |
|------|---------------------|
| `config/scripts/orchestrator_next/record.py` | ADD `_resolve_feature_metrics`, `_write_feature_metrics`; ADD trigger at `mark-change-completed` |
| `config/scripts/orchestrator_next/upsert.py` | No changes — `upsert_feature_metrics` is the existing write target |
| `scripts/inline/ingest-feature-metrics.py` | DELETE (after absorption verified) |
| `config/steps/ingest-feature-metrics.yaml` | DELETE |
| `config/workflows/_complete-phase.yaml` | REMOVE the `ingest-feature-metrics` line |
| `config/tests/test-complete-phase-order.sh` | REWRITE — remove `ingest-feature-metrics` assertions, update invariant |
| `config/scripts/__tests__/test-ingest-feature-metrics.sh` | DELETE (test covers the deleted script) |
| `config/scripts/verify-all.sh` | REMOVE `test-ingest-feature-metrics.sh` entry (lines 107-108) |
| `config/scripts/__tests__/fixtures/baseline_compute_swe_metrics.yaml` | REMOVE `ingest-feature-metrics` key |
| `config/scripts/orchestrator_next/tests/test_feature_metrics_parity.py` | NEW — parity test (TDD, must go RED before implementation) |

### Build-or-Reuse Decision

Reuse (extend existing code). All computation logic exists in `ingest-feature-metrics.py` and can be moved without modification into `record.py`. The write target `upsert_feature_metrics` in `upsert.py` is already the correct interface. This is a consolidation task, not new capability.
