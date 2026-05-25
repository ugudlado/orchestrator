# Phase review: ORC-81 (implement)

**Verdict:** pass

## Summary

All eight implementation tasks (T-1–T-8) completed. Reconcile FR-4/FR-5 now drop or skip
`in_progress` rows whose `step_id` is absent from `workflow_plan` (via `phase_nodes`).
Dispatch resume refuses out-of-plan ghosts with exit 3. `bin/orchestrator` persists
reconcile mutations to `state.yaml`.

## Verification

```
pytest scripts/orchestrator_next/tests/test_reconcile_workflow_plan_membership.py \
  scripts/orchestrator_next/tests/test_dispatch_resume_plan_membership.py \
  scripts/orchestrator_next/tests/test_bin_orchestrator_persist_reconcile.py \
  scripts/orchestrator_next/tests/test_orc81_end_to_end.py \
  scripts/orchestrator_next/tests/test_reconcile_in_progress.py \
  scripts/orchestrator_next/tests/test_reconcile_terminal_skip.py -q
```

18 passed.

## Acceptance criteria

| AC | Status |
|----|--------|
| FR-4 strips plan-absent in_progress (YAML) | pass |
| FR-5 skips plan-absent DB materialisation | pass |
| Caller persists reconcile strip | pass |
| Dispatch resume exit 3 on ghost | pass |
| Legacy `active:` plan shape | pass |
| Regression tests | pass |

## Score

| Dimension | Score |
|-----------|-------|
| spec_compliance | 9 |
| correctness | 9 |
| security | 9 |
| simplicity | 9 |
| code_quality | 9 |
| **overall** | **9** |
