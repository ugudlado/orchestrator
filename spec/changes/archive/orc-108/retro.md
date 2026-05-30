---
feature-id: orc-108
phase: complete
retro_date: 2026-05-31
---

# Retro — ORC-108: Replace task injection with explicit execute-tasks step

## Summary

### What went well

- **Zero rework**: All 10 tasks completed at attempt 1. No retries, no blocked steps, no mid-flight scope changes.
- **TDD discipline held**: RED tests (T-1, T-5) written first; implementation followed (T-2/T-3, T-6/T-7); integration gates (T-4, T-10) confirmed GREEN at each checkpoint. The structure made each task's scope obvious and its verify commands reliable.
- **Design accuracy**: Predicted 10 tasks, actual 10 tasks (100%). Design complexity estimate (S) matched execution reality.
- **Phase review first pass**: 9/10, no retries, all 6 ACs verified with evidence. 747 tests passed.
- **clean deletion**: complete_phase.py (148 lines) replaced by check-implement-complete.py (~100 lines). No orphan imports, no dangling references. Grep confirmed safety before deletion in T-1 research.
- **Static schema declaration**: Adding complete-phase steps directly to feature.yaml/bugfix.yaml tails made the DAG fully readable without runtime surgery. The dispatcher now walks the complete-phase chain without any injection logic.

### What to improve

1. **xfail markers not removed in integration gate**: 9 tests carry `@pytest.mark.xfail(strict=False)` from the RED phase. T-10 (integration gate) was the correct place to strip these markers — it was not in the task's file list. These are now xpassing tests that quietly mislead readers about test intent. Needs a chore commit.

2. **capture-test-baseline silent path mismatch**: The step used `config/scripts/orchestrator_next/tests/` (wrong path) instead of `orchestrator_next/tests/`. Result: baseline recorded as `skipped: true, reason: unparseable, exit_code: 4`. The baseline was silently lost for this entire feature run. The script should read the test path from `project.yaml verify_commands.test`.

---

## ISSUE-1

**title**: Remove xfail markers from RED-phase tests after GREEN confirmation
**severity**: low
**fix_direction**: >
  In the final integration-gate task (T-N), include the test files that carry
  @pytest.mark.xfail(strict=False) markers in the task's file list and verify
  commands. Remove _EXECUTE_TASKS_XFAIL and _COMPLETE_STEPS_XFAIL marker
  definitions and all @pytest.mark.xfail decorators from
  orchestrator_next/tests/test_expand_plan.py and
  orchestrator_next/tests/test_complete_workflow_contract.py.
  Alternatively, emit a chore(orc-108) commit immediately after phase review passes.
**backlog_candidate**: true

---

## ISSUE-2

**title**: capture-test-baseline uses hardcoded path that diverges from project.yaml verify_commands.test
**severity**: medium
**fix_direction**: >
  Edit the capture-test-baseline inline script to read the test path from
  project.yaml verify_commands.test rather than hardcoding
  `config/scripts/orchestrator_next/tests/`. The current path is stale and
  causes exit_code 4 on every run, silently marking the baseline as unparseable.
  This means every feature run since the path changed has no valid test baseline.
**backlog_candidate**: true

---

## Cycle Metrics

| Metric | Value |
|--------|-------|
| predicted_tasks | 10 |
| actual_tasks | 10 |
| task_accuracy_pct | 100.0 |
| fix_task_count | 0 |
| rework_rate | 0.0% |
| review_score | 9/10 |
| review_retries | 0 |
| file_overlap_pct | 0.0% (dispatcher recording gap — not an agent miss) |
| total_steps | 22 |
| steps_at_attempt_gt_1 | 0 |

## Key Learnings

1. **xfail-cleanup-is-part-of-tdd-task**: Include xfail marker removal in the final integration-gate task's file list and verify commands, not as a post-review chore.
2. **capture-test-baseline-path-must-match-project-yaml**: capture-test-baseline should read the test path from `project.yaml verify_commands.test` to stay in sync with the canonical test runner path.
3. **schema-static-complete-phase-steps**: Declare complete-phase steps statically in feature/bugfix schema tails. This eliminates complete_phase.py injection logic and makes the full DAG readable from the schema file.
