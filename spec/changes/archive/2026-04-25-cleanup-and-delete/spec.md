---
feature-id: cleanup-and-delete
linear-ticket: N/A
---

# Specification: Absorb ingest-feature-metrics into `done` (Phase 5 of workflow-engine-as-state-machine)

## Motivation

Phase 5 finishes the metrics-pipeline consolidation that Phases 3 and 4 began. Phase 4 absorbed `ingest-driver-auto` and `ingest-subagents-auto` into the `done` verb; the result is one CLI verb that writes `step_events`, `phase_events`, `driver_sessions`, and per-subagent synthetic step rows atomically at the right level boundaries. The last hold-out is `scripts/inline/ingest-feature-metrics.py` — a 440-line standalone script that reads `tasks.md`, `state.yaml`, and `git log` and writes one row to `feature_metrics`. Today it runs as a separate inline step in `_complete-phase.yaml` between `mark-change-completed` and `compute-swe-metrics`. That separation makes the write a fail-soft afterthought, leaves the standalone script as dead surface area for drivers to think about, and breaks the consistency story Phase 4 established — `done` is supposed to own boundary writes, but the feature-level row still comes from a separate verb.

After absorption, `done` writes the `feature_metrics` row atomically when it records `mark-change-completed` completion. The trigger point is critical: `compute-swe-metrics` runs two positions later in the same phase and reads `feature_metrics` via a LEFT JOIN, so the row must be present before the existing complete-phase ordering hits `compute-swe-metrics`. That fixes the Phase 4 "feature boundary" mismatch — `remove-worktree` (the workflow's actual last step) is too late, and special-casing the trigger inside `done` is simpler than introducing a new step-contract metadata field for one step. The standalone script, its step contract, the entry in `_complete-phase.yaml`, and the script-level test are deleted in a follow-up stage once the absorbed path is proven by parity test.

## What Changes

- New helper pair `_resolve_feature_metrics(state, change_id)` and `_write_feature_metrics(db, repo_root, change_id, data)` added to `config/scripts/orchestrator_next/record.py`, mirroring the Phase 4 pattern of `_resolve_driver_session` / `_write_driver_session`.
- The 6 computation functions (`parse_tasks`, `compute_retries`, `compute_resolution`, `run_git_churn`, `extract_review_scores`, `wall_clock_minutes`) move from `scripts/inline/ingest-feature-metrics.py` into `record.py` verbatim — same signatures, same logic.
- New trigger inside `record()`: when `step_id == "mark-change-completed"` and `status == "completed"`, `_resolve_feature_metrics` runs OUTSIDE the boundary transaction and `_write_feature_metrics` runs INSIDE the same DuckDB transaction as the `step_events` upsert for `mark-change-completed`. This is a special-case dispatch (not a new step-contract field) because the trigger applies to exactly one step.
- Failure mode: fatal-on-failure inside the transaction, matching Phase 4's boundary-write semantics. The current script's "fail loud, continue" was a workaround for being a separate fail-soft inline step; once the write is inside `done`'s transaction, atomicity wins.
- Delete `scripts/inline/ingest-feature-metrics.py`, `config/steps/ingest-feature-metrics.yaml`, and the `- ingest-feature-metrics` line from `config/workflows/_complete-phase.yaml`.
- Rewrite `config/tests/test-complete-phase-order.sh`: drop assertions about `ingest-feature-metrics` presence/ordering; keep the surviving invariant that `mark-change-completed` precedes `compute-swe-metrics` and that the removed step is absent.
- Delete `config/scripts/__tests__/test-ingest-feature-metrics.sh` and remove its entry from `config/scripts/verify-all.sh:107-108`.
- Update `config/scripts/__tests__/fixtures/baseline_compute_swe_metrics.yaml:81` to drop the `ingest-feature-metrics` step-list key.
- New parity test `config/scripts/orchestrator_next/tests/test_feature_metrics_parity.py` runs both implementations against the `done-verb-level-aware-writes` archive fixture and diffs all 24 non-audit columns (cycle-20 rule).

## Requirements

### Functional

1. **FR-1**: `record.py` MUST expose `_resolve_feature_metrics(state, change_id)` as a pure-compute function. It MUST call `parse_tasks`, `compute_retries`, `compute_resolution`, `run_git_churn`, `extract_review_scores`, and `wall_clock_minutes` — the 6 functions moved from `ingest-feature-metrics.py` — and return a dict shaped for `upsert_feature_metrics(db, repo_root, change_id, **fields)`. The function MUST NOT open a DuckDB connection or issue BEGIN/COMMIT.
2. **FR-2**: `record.py` MUST expose `_write_feature_metrics(db, repo_root, change_id, data)` which calls `upsert_feature_metrics(db, repo_root=..., change_id=..., **data)`. Caller controls the transaction.
3. **FR-3**: When `record()` is invoked with `step_id == "mark-change-completed"` and `status == "completed"`, `done` MUST resolve the feature-metrics dict via `_resolve_feature_metrics` BEFORE issuing `BEGIN`, then write `step_events` and `feature_metrics` inside one DuckDB transaction (BEGIN → step_events upsert → feature_metrics insert → COMMIT; ROLLBACK on any error inside the block). On non-`mark-change-completed` steps the path is unchanged from Phase 4.
4. **FR-4**: Failure mode for the `mark-change-completed` transaction MUST be fatal: any exception inside the BEGIN/COMMIT block triggers ROLLBACK and a non-zero exit code from `record()` (consistent with Phase 4 NFR-3). `git log` failures inside `run_git_churn` MUST remain non-fatal (return zeros) — that policy lives inside the helper, not in the trigger.
5. **FR-5**: For schemas without a `tasks.md` (spike/autopilot), `_resolve_feature_metrics` MUST emit NULL task columns (matching the current script's behavior at `ingest-feature-metrics.py:374-381`) rather than raising. For `feature` and `bugfix` schemas, a missing `tasks.md` at `tasks_path` (or the `.state/<slug>/tasks.md` fallback) MUST cause `_resolve_feature_metrics` to raise so the trigger transaction rolls back. Missing `started_at` or `completed_at` on a `feature`/`bugfix` schema MUST raise (preserves the existing fail-loud invariant for required state fields).
6. **FR-6**: `feature_metrics.source` MUST be set to `"done@<utcnow_iso>"` on writes from the absorbed path (replacing the legacy `"ingest-feature-metrics@<utcnow>"`). This single column is the only legitimate value-difference vs. the legacy script and is excluded from the parity test.
7. **FR-7**: `config/workflows/_complete-phase.yaml` MUST NOT contain `- ingest-feature-metrics` after Stage B. `config/steps/ingest-feature-metrics.yaml` and `scripts/inline/ingest-feature-metrics.py` MUST be deleted.
8. **FR-8**: `config/tests/test-complete-phase-order.sh` MUST be rewritten to (a) assert `mark-change-completed` precedes `compute-swe-metrics`; (b) assert `ingest-feature-metrics` is absent from the step list; (c) keep all other surviving ordering assertions intact.
9. **FR-9**: `config/scripts/__tests__/test-ingest-feature-metrics.sh` MUST be deleted and its entry removed from `config/scripts/verify-all.sh` (lines 107-108). `config/scripts/__tests__/fixtures/baseline_compute_swe_metrics.yaml` MUST drop the `ingest-feature-metrics:` key from the fixture step list (line 81).
10. **FR-10**: A parity test `config/scripts/orchestrator_next/tests/test_feature_metrics_parity.py` MUST run BOTH implementations against the archived fixture at `spec/changes/archive/2026-04-25-done-verb-level-aware-writes/` and assert byte-equivalent values across all 24 non-audit columns (`source` excluded). The test MUST fail before Stage A lands (helpers don't exist) and pass after.

### Non-Functional

1. **NFR-1**: The `mark-change-completed` boundary write MUST be atomic — either both the step_events and the feature_metrics rows commit, or neither does. Partial success is forbidden because `compute-swe-metrics` (two positions later) reads `feature_metrics` and must observe a complete row whenever it observes the corresponding step row.
2. **NFR-2**: All new SQL paths use parameterised `db.execute(sql, params)` calls (existing `upsert_feature_metrics` already complies — no new SQL is authored in this feature; the helper just calls it). `change_id` slug validation is enforced by `upsert_feature_metrics` itself.
3. **NFR-3**: Bootstrap safety: this very feature's `complete` phase will run with the OLD `_complete-phase.yaml` step list intact (containing `ingest-feature-metrics`) until Stage B deletes it. Stage A leaves the inline script working alongside the new helper path so an in-flight workflow cannot lose its `feature_metrics` row mid-rollout.
4. **NFR-4**: Performance impact: the `mark-change-completed` trigger adds one git-log subprocess (10s timeout) plus tasks.md / state.yaml parsing, which already happens once per feature in the current script. Net cost is unchanged — the work moves, it doesn't duplicate. Target: p99 < 500 ms added latency on `mark-change-completed` for a typical feature, dominated by the existing `run_git_churn` budget.

## Architecture

| File | Change |
|------|--------|
| `config/scripts/orchestrator_next/record.py` | ADD 6 computation functions (`parse_tasks`, `compute_retries`, `compute_resolution`, `run_git_churn`, `extract_review_scores`, `wall_clock_minutes`) lifted verbatim from `ingest-feature-metrics.py`. ADD `_resolve_feature_metrics(state, change_id)` and `_write_feature_metrics(db, repo_root, change_id, data)`. Extend the existing `record()` boundary block: when `step_id == "mark-change-completed"` and `status == "completed"`, run resolve OUTSIDE the BEGIN, write `step_events` + `feature_metrics` INSIDE one BEGIN/COMMIT, ROLLBACK fatal on failure. The Phase 4 phase-/feature-boundary path on `remove-worktree` stays unchanged. |
| `config/scripts/orchestrator_next/upsert.py` | No change — `upsert_feature_metrics` is the existing write target. |
| `scripts/inline/ingest-feature-metrics.py` | DELETE in Stage B. |
| `config/steps/ingest-feature-metrics.yaml` | DELETE in Stage B. |
| `config/workflows/_complete-phase.yaml` | REMOVE the `- ingest-feature-metrics` line in Stage B (current line 20). |
| `config/tests/test-complete-phase-order.sh` | REWRITE in Stage B — drop `ingest-feature-metrics` presence/ordering assertions, keep `mark-change-completed → compute-swe-metrics` invariant, add absence assertion. |
| `config/scripts/__tests__/test-ingest-feature-metrics.sh` | DELETE in Stage B. |
| `config/scripts/verify-all.sh` | REMOVE the `test-ingest-feature-metrics.sh` entry at lines 107-108 in Stage B. |
| `config/scripts/__tests__/fixtures/baseline_compute_swe_metrics.yaml` | REMOVE the `ingest-feature-metrics:` key (line 81) in Stage B. |
| `config/scripts/orchestrator_next/tests/test_feature_metrics_parity.py` | NEW — parity test. Created RED in Stage A (helpers missing), passes GREEN once Stage A lands. |
| `config/scripts/orchestrator_next/tests/test_feature_metrics_trigger.py` | NEW — covers FR-3/FR-4/FR-5 (trigger logic, atomic ROLLBACK, schema-aware NULL behavior). |

## Test Strategy

### Test File Paths

| Component | Test file |
|-----------|-----------|
| 6 computation functions ported from `ingest-feature-metrics.py` | `config/scripts/orchestrator_next/tests/test_feature_metrics_compute.py` (NEW) |
| `_resolve_feature_metrics` shape + dispatch | `config/scripts/orchestrator_next/tests/test_feature_metrics_compute.py` (same file) |
| `_write_feature_metrics` calls `upsert_feature_metrics` | `config/scripts/orchestrator_next/tests/test_feature_metrics_compute.py` (same file) |
| `record()` `mark-change-completed` trigger + atomic ROLLBACK | `config/scripts/orchestrator_next/tests/test_feature_metrics_trigger.py` (NEW) |
| Parity vs. legacy script (cycle-20 rule) | `config/scripts/orchestrator_next/tests/test_feature_metrics_parity.py` (NEW) |
| `_complete-phase.yaml` step ordering after deletion | `config/tests/test-complete-phase-order.sh` (REWRITE) |

### Coverage Targets

90% overall on changed files. 100% on `_resolve_feature_metrics` schema-branch logic (feature/bugfix vs. spike) and on the trigger's BEGIN/COMMIT/ROLLBACK paths.

### Key Test Scenarios

- `mark-change-completed` with `status: completed` on a `feature` schema → `step_events` and `feature_metrics` rows both present in DuckDB; exit 0.
- `mark-change-completed` with `status: completed` on a `feature` schema where `_write_feature_metrics` raises (mocked) → no `step_events` row remains for that call (ROLLBACK verified by COUNT); exit non-zero.
- `mark-change-completed` on a `spike` schema (no `tasks.md`) → `feature_metrics` row written with NULL task columns; exit 0.
- `mark-change-completed` on a `feature` schema missing `tasks.md` → resolve raises BEFORE `BEGIN`; no rows written; exit non-zero.
- `mark-change-completed` with git-log subprocess timeout → row written with zero churn columns; exit 0 (non-fatal `run_git_churn` policy preserved).
- Parity: run legacy script and `_resolve_feature_metrics` + `_write_feature_metrics` against the `done-verb-level-aware-writes` fixture; all 24 non-audit columns match exactly.
- Step ordering: after Stage B, `test-complete-phase-order.sh` passes; `ingest-feature-metrics` is absent from the step list and `mark-change-completed` precedes `compute-swe-metrics`.

## Acceptance Criteria

- AC-1: Given a `feature`-schema workflow at the `mark-change-completed` step with `status: completed`, when `orchestrator done` runs, then a `feature_metrics` row is visible in DuckDB with the same columns the legacy script would have produced (excluding `source`) and exit code is 0. [traces: UC-1, UC-2]
- AC-2: Given a `mark-change-completed` payload where `_write_feature_metrics` raises (DuckDB lock or schema error), when `orchestrator done` runs, then exit code is non-zero AND no `step_events` row is left committed for that call (atomic ROLLBACK verified by SELECT COUNT). [traces: UC-E2]
- AC-3: Given a `spike` schema with no `tasks.md` at `mark-change-completed`, when `orchestrator done` runs, then `_resolve_feature_metrics` produces a dict with NULL task columns and a `feature_metrics` row is written with task columns NULL; exit code is 0. [traces: UC-3]
- AC-4: Given a `feature` schema with `tasks.md` missing from both `tasks_path` and the `.state/<slug>/tasks.md` fallback at `mark-change-completed`, when `orchestrator done` runs, then `_resolve_feature_metrics` raises BEFORE the transaction opens, no rows are written, and exit code is non-zero. [traces: UC-E1]
- AC-5: Given a `mark-change-completed` payload and a `git log` invocation that times out, when `orchestrator done` runs, then `run_git_churn` returns zeros, the `feature_metrics` row is written with zero churn columns, and exit code is 0. [traces: UC-E4]
- AC-6: Given the parity test runs both legacy `ingest-feature-metrics.py` and `_resolve_feature_metrics` + `_write_feature_metrics` against `spec/changes/archive/2026-04-25-done-verb-level-aware-writes/`, when results are diffed, then all 24 non-audit columns (`source` excluded) match byte-for-byte. [traces: UC-1, UC-2]
- AC-7: Given Stage B is shipped, when `cat config/workflows/_complete-phase.yaml` is read, then no line containing `ingest-feature-metrics` is present and `mark-change-completed` still precedes `compute-swe-metrics`. [traces: UC-E3]
- AC-8: Given Stage B is shipped, when `bash scripts/verify-all.sh` runs, then it succeeds without referencing the deleted `test-ingest-feature-metrics.sh`. [traces: UC-1]
- AC-9: Given the in-flight `cleanup-and-delete` workflow's own `complete` phase runs (Stage A is shipped, Stage B is not yet), when its `_complete-phase.yaml` invokes `ingest-feature-metrics`, then the inline script still works and the standalone `feature_metrics` row is written, satisfying the bootstrap-safety contract. [traces: UC-E3]

## Alternatives Considered

**Alternative 1: New step-contract metadata field `triggers_feature_metrics: true`**
Rejected. Generalizing a one-off trigger into a metadata field adds machinery (contract schema change + dispatch lookup) for zero additional callers. The single special-case `if step_id == "mark-change-completed"` inside `record()` is two lines and reads obvious; introducing the field would also force a new contracts-schema validation pass downstream. Discovery OQ-1 explicitly framed this as Option A vs. Option B; Option A wins on simplicity.

**Alternative 2: Fire at the generic feature boundary (`remove-worktree`) and reorder `compute-swe-metrics`**
Rejected — and discovery already rejected it. `compute-swe-metrics` reads `feature_metrics` via LEFT JOIN; firing at `remove-worktree` (position 7) puts the row write after `compute-swe-metrics` (position 5), so the join would always see NULL. Reordering `compute-swe-metrics` after `remove-worktree` is also rejected because `compute-swe-metrics` runs git churn against the worktree that `remove-worktree` deletes.

**Alternative 3: Keep `ingest-feature-metrics.py` as a thin wrapper that calls the new helpers**
Rejected. The point of Phase 5 is removing the standalone script, not preserving it. A wrapper leaves the inline-step machinery alive and contradicts the consolidation goal.

**Alternative 4: Move the 6 computation functions into a new sibling module `feature_metrics.py`**
Rejected for Phase 5 scope. Adds a new module file when the functions are ~250 lines total and called from exactly one place (`record.py`). The Phase 4 precedent placed `_resolve_driver_session` and the lifted JSONL-parsing logic directly in `record.py` — same pattern here keeps the diff small and the call graph local. A future cosmetic split is fine but not required.

**Alternative 5: Fail-soft (warn + continue) for `_write_feature_metrics`**
Rejected. The current script's fail-loud-then-continue is a workaround for being a separate inline step. Once the write is inside `done`'s transaction with the `mark-change-completed` step row, atomicity is the right semantic — `compute-swe-metrics` two positions later assumes a complete row. Discovery OQ-2 framed this; Phase 4 used fatal for the same reason.

## Impact

- Breaking changes: none during Stage A. After Stage B, `bin/orchestrator` no longer dispatches to `ingest-feature-metrics.py` — but no caller outside the deleted `_complete-phase.yaml` step references it (verified by grep across `config/`, `scripts/`, `agents/`, `skills/`, `spec/`).
- Migration: existing `feature_metrics` rows are unchanged. The trigger uses INSERT OR REPLACE keyed on `(repo_root, change_id)` so re-runs are idempotent.
- Affected areas: complete-phase step list (shrinks by one step), `record.py` (gains 6 computation functions + 2 helpers + trigger branch), test surface (gains parity + trigger tests, drops legacy script test).
- Worktree: this feature's own `complete` phase MUST land Stage A first; Stage B's deletion is the last commit so the in-flight workflow keeps using the inline script through its own complete phase (NFR-3 / UC-E3).

## Decisions

- Trigger point: special-case `step_id == "mark-change-completed"` inside `record()` (Discovery OQ-1, Option A). One step, one branch, no new contract field.
- Failure mode: fatal-on-failure (Discovery OQ-2). Matches Phase 4 NFR-3.
- Transaction scope: the `feature_metrics` write is inside the same DuckDB transaction as the `step_events` upsert for `mark-change-completed`. Resolve runs OUTSIDE BEGIN to keep the transaction window short (mirrors Phase 4 subagent pattern).
- Helper layout: 6 computation functions move into `record.py` verbatim (no signature changes); helper pair `_resolve_feature_metrics` / `_write_feature_metrics` mirrors Phase 4's `_resolve_driver_session` / `_write_driver_session` pair.
- Stage layout: Stage A (additive — helpers + trigger + parity test, both paths coexist); Stage B (deletion — inline script, step contract, complete-phase entry, legacy test, fixture key, verify-all entry). Self-bootstrapping handled by Stage A keeping the inline script live until Stage B lands.
- Parity fixture: `spec/changes/archive/2026-04-25-done-verb-level-aware-writes/` (the most recent feature-schema archive with `state.yaml` and `tasks.md`). Diff all 24 non-audit columns; exclude `source` (legitimately differs by FR-6).
- DDL: no migration. `feature_metrics` already has all 25 columns from earlier phases (verified via `DESCRIBE feature_metrics` against live DB).

<!-- Format contract: contracts/artifact-formats.md § Specification Format Contract -->
