---
id: ORC-9
title: '`dispatch.py._find_completed_step` ignores `repeat_until` predicate'
status: Done
assignee: []
created_date: '2026-05-03 10:55'
updated_date: '2026-05-09 21:19'
labels:
  - slug-dispatch-repeat-until-honor
  - feature
  - score-8.5
  - recurrence-2
dependencies: []
references:
  - single-source-metrics-via-step-events retro (2026-04-19) ISSUE-33
  - >-
    Evaluator confirmed at
    `config/scripts/orchestrator_next/dispatch.py:140-147`
priority: medium
ordinal: 8000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
<!-- migrated from spec/changes/backlog.md slug: dispatch-repeat-until-honor -->

**Original score:** 8.5 | **Recurrence:** 2

## Idea

`config/scripts/orchestrator_next/dispatch.py::_find_completed_step` (lines ~140–147) returns `True` on any completed step_history entry for the given (phase, step_id). For steps declared with `repeat_until: <predicate>` (e.g., `execute-next-task` with `repeat_until: all_tasks_completed`), this causes `orchestrator next` to skip straight to the following step after the first task completes — even when many tasks remain.

The `repeat_until` predicate is only evaluated in `record.py` (which sets an advisory `next_step` in state.yaml). `dispatch.py` ignores state.yaml's `next_step` and recomputes from `workflow_plan[phase].active` minus completed entries → repeat_until semantics are lost at dispatch time.

## Why Now

Affects every workflow that uses `repeat_until`. Currently only `execute-next-task` has this, so the impact is narrow in scope but mid-flight blocking when hit. Without this, any autopilot or manual /implement run requires driver-side bookkeeping surgery after task 1.

## Scope

1. In `dispatch.py`, teach `_find_completed_step` (or its caller) to consult the step contract's `repeat_until` predicate. If a step has `repeat_until` AND the predicate returns False, treat the step as not-completed and return it.
2. Use `_REPEAT_PREDICATES` from `record.py` (shared registry: `all_tasks_completed`, etc.) — do not duplicate the predicate logic.
3. Test: `test_dispatch.py::test_repeat_until_keeps_step_active` — seed `execute-next-task` with one completed entry + tasks.md containing unchecked tasks; assert `orchestrator next` returns `execute-next-task` again, not the following step.
4. Migration: existing in-flight workflows may have phantom `execute-next-task` entries that were never cleaned up. The fix is backward-compatible: once dispatch honors repeat_until, stale entries become harmless.

## Scope estimate

~30 lines Python + one test. Chore-tier.

## Source

- single-source-metrics-via-step-events retro (2026-04-19) ISSUE-33
- Evaluator confirmed at `config/scripts/orchestrator_next/dispatch.py:140-147`

---
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 In `dispatch.py`, teach `_find_completed_step` (or its caller) to consult the step contract's `repeat_until` predicate. If a step has `repeat_until` AND the predicate returns False, treat the step as not-completed and return it.
- [ ] #2 Use `_REPEAT_PREDICATES` from `record.py` (shared registry: `all_tasks_completed`, etc.) — do not duplicate the predicate logic.
- [ ] #3 Test: `test_dispatch.py::test_repeat_until_keeps_step_active` — seed `execute-next-task` with one completed entry + tasks.md containing unchecked tasks; assert `orchestrator next` returns `execute-next-task` again, not the following step.
- [ ] #4 Migration: existing in-flight workflows may have phantom `execute-next-task` entries that were never cleaned up. The fix is backward-compatible: once dispatch honors repeat_until, stale entries become harmless.
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Bug was already fixed in HL-303 (commit 7bdd31c) before ORC-9 workflow was initialized. Both seams fixed: (1) record.py::_compute_next_step now consults repeat_until predicate, (2) dispatch.py history-walk loop re-emits step when predicate returns False. All 4 regression tests pass. Archived 2026-05-10-orc-9.
<!-- SECTION:FINAL_SUMMARY:END -->
