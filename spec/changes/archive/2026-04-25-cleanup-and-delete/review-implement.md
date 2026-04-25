# Phase Review — implement — cleanup-and-delete

**Phase**: implement  
**Schema**: feature  
**Review round**: 2  
**Date**: 2026-04-25  
**Verdict**: PASS

---

## Round Delta

| Round | Score | Verdict | Critical | Important | Minor |
|-------|-------|---------|----------|-----------|-------|
| 1 | 5 | FAIL | 1 (CF-1) | 0 | 4 |
| 2 | 9 | PASS | 0 | 0 | 4 (carried) |

---

## Dimension Scores

| Dimension | Score | Key Issues |
|-----------|-------|------------|
| spec_compliance | 9 | AC-9 intent preserved (see note); all other ACs pass |
| correctness | 9 | CF-1 resolved: dispatch.py:352-365 has try/except guarding `load_contract_for_step`; record.py:923-926 and 995-998 were already guarded pre-fix |
| security | 9 | No new SQL authored; parameterised writes; no hardcoded secrets |
| simplicity | 9 | Minimal 5-line try/except mirroring existing resume_step pattern at 282-289; no new abstractions |
| code_quality | 9 | Test count: 342 passed, 2 pre-existing failures (delta +1); minor findings from round 1 carried but non-blocking |
| **Overall** | **9** | Green base achieved; no critical/important findings |

---

## Verification Results

### FT-20 Fix — dispatch.py

- **Location**: `config/scripts/orchestrator_next/dispatch.py:352-365`
- **Pattern**: try/except `FileNotFoundError` wrapping `load_contract_for_step(next_step_id, state_yaml_path)` at line 354, falling back to `StepContract(id=next_step_id, agent="inline", run=None, instruction="", rules=[])`.
- **Mirrors**: `dispatch.py:281-291` (resume_step branch) exactly — same exception type, same stub shape.
- **Stub shape differences**: resume_step uses `agent=last.agent or "inline"`; run_step uses hardcoded `agent="inline"`. Correct — no `last` entry exists for a not-yet-started orphan step.

### Regression Test

- **Command**: `pytest config/scripts/orchestrator_next/tests/test_dispatch_missing_contract.py -xvs`
- **Result**: 1 passed
- **Assertion**: `exit_code==0`, `action['step_id']=='step-deleted-from-disk'`, `action['agent']=='inline'`, `action['run'] is None`

### E2E Scenario (simulated)

Constructed state.yaml with `phase=complete`, `workflow_plan.complete.active=['ingest-feature-metrics', 'compute-swe-metrics']`, empty step_history, `ORCHESTRATOR_HOME=worktree`. Called `dispatch(state, state_yaml_path)` directly.

- **Result**: `exit_code=0`, `action='run_inline'`, `step_id='ingest-feature-metrics'`, `agent='inline'`
- **No FileNotFoundError raised.** Complete phase would not crash.

### `orchestrator done` (record path) — Caller-site audit

All `load_contract_for_step` call sites enumerated:

| File | Line | Guarded? | Fallback |
|------|------|----------|----------|
| dispatch.py | 282 | Yes (pre-existing) | `StepContract(id=step_id, agent=last.agent or "inline", ...)` |
| dispatch.py | 354 | Yes (FT-20 fix) | `StepContract(id=next_step_id, agent="inline", ...)` |
| record.py | 924 | Yes (pre-existing) | `contract = None` |
| record.py | 996 | Yes (pre-existing) | `contract = None` |

The `done` command path (`record.py:924` and `record.py:996`) was already guarded before FT-20. The fix did not introduce a new unguarded call site downstream. Complete scenario: dispatch returns stub → driver calls `orchestrator done` with step_id `ingest-feature-metrics` → record.py:996 catches FileNotFoundError → validates outputs against `contract=None` (no required outputs) → records step as completed → dispatch advances to next step.

### Test Suite

- **Command**: `pytest config/scripts/orchestrator_next/tests/ -q`
- **Result**: 342 passed, 2 failed
- **Failures**: `test_archive_backlog_cleanup.py::TestArchiveBacklogCleanup::test_backlog_dir_removed_after_archive` and `::test_cleanup_commit_in_git_log` — both pre-existing (confirmed round 1 baseline: 341 passed)
- **Net delta**: +1 new test (`test_dispatch_missing_contract.py::test_dispatch_falls_back_when_contract_missing`)

### Gates

- **`bash scripts/m8-gates.sh`**: exits 0 — Gate 4 at 9 inline scripts (correct post-deletion), all gates pass
- **`bash config/tests/test-complete-phase-order.sh`**: exits 0, 13 passed, 0 failed; `ingest-feature-metrics` confirmed absent; `mark-change-completed` (pos 3) precedes `compute-swe-metrics` (pos 4)

---

## AC Verification (full re-run)

| AC | Criterion (summary) | Status | Evidence |
|----|---------------------|--------|----------|
| AC-1 | feature-schema `mark-change-completed` writes `feature_metrics` row, exit 0 | PASS | trigger tests pass; parity test passes (unchanged from round 1) |
| AC-2 | `_write_feature_metrics` raises → ROLLBACK, no `step_events` row, non-zero exit | PASS | `test_write_feature_metrics_raises_rolls_back` passes (unchanged) |
| AC-3 | spike schema no tasks.md → NULL task columns written, exit 0 | PASS | trigger test covers spike schema branch (unchanged) |
| AC-4 | feature schema missing tasks.md → raises BEFORE transaction, no rows, non-zero | PASS | trigger test covers this (unchanged) |
| AC-5 | git-log timeout → zero churn, row written, exit 0 | PASS | trigger test (e) passes (unchanged) |
| AC-6 | parity: 24 non-audit columns match snapshot | PASS | `test_parity_against_snapshot` passes (unchanged) |
| AC-7 | `_complete-phase.yaml` has no `ingest-feature-metrics`, ordering correct | PASS | test-complete-phase-order.sh exits 0, 13 passed |
| AC-8 | `verify-all.sh` runs without referencing deleted test | PASS | grep confirms no entry (unchanged) |
| AC-9 | Bootstrap safety: in-flight complete phase can advance past `ingest-feature-metrics` after contract deletion | PASS (intent) | Literal precondition "Stage B not yet landed" is superseded — Stage B landed during implement. FT-20 preserves the NFR-3 bootstrap-safety intent: dispatch falls back to inline stub, `orchestrator done` records the step via pre-existing FileNotFoundError guard in record.py:996, workflow advances to `compute-swe-metrics`. E2E scenario confirmed. |

**AC summary**: 9 passed (8 literal + 1 intent-based), 0 failed.

---

## Critical Findings

None.

---

## Important Findings

None.

---

## Minor Findings (carried from round 1 — not blocking, not escalated)

### MF-1 — Gate 4 driver follow-up (f6b0caf) — process quality

Gate 4 count was left at 10 after T-14 deleted `ingest-feature-metrics.py`; fixed by driver as a separate commit. T-14 should have updated `scripts/m8-gates.sh` atomically. Fix is in place; gate passes at 9.

### MF-2 — TDD audit trail gaps

T-3 and T-5 tests written in same commit as T-1; no separate RED commit for T-9. Tests cover correct behavior and all pass. Procedural gap, no substantive correctness impact.

### MF-3 — Batch task completion — auditability

19 tasks batched into one `execute-next-task` step_history entry; per-task `task_checkpoint` fields absent. No behavioral impact.

### MF-4 — verify-all.sh sandbox failure

`bash config/scripts/verify-all.sh` exits 1 due to sandbox mktemp restriction on `/tmp` in `test-orchestrator-metrics-json-shape.sh`. Not introduced by this feature; pre-existing sandbox restriction. Python test suite passes.

---

## Baseline Comparison

Historical `review_score_avg` across 6 archived feature-schema features: 8.97. Current round-2 overall: 9. Regression from round 1 (5) is fully resolved.

---

## retries.run-phase-review

Previously at 1 (round 1 increment). Not incrementing — round 2 is a PASS.

---

## Fix Tasks

None.

