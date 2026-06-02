# Design Review: ORC-119 — DAG walker on_failure routing fix

## Verdict: PASS

## Scores

| Dimension     | Score | Notes |
|---------------|-------|-------|
| completeness  | 10    | All sections present and substantive; all open questions closed |
| ac_coverage   | 10    | Every AC traces to a task; every task's `why` cites AC IDs |
| task_quality  | 9     | Tasks are small and scoped; verify commands present; no TDD RED-phase xfail issues; minor: fix-1 is a meta-task that self-modifies tasks.yaml post-T-3, but depends_on ordering makes it safe |
| feasibility   | 10    | 2-line guard at one chokepoint; sole-writer invariant verified by grep at HEAD; no missing dependencies |
| scope_control | 10    | Non-Goals explicitly exclude routing, retry-cap, step_history mutations; all tasks within stated Goals |

**Overall: 9**

## Summary

The design is precise and well-reasoned. It correctly identifies the root cause
(read/write disagreement between `record.py`'s `pending` write and
`_effective_node_status`'s `step_history` override), selects the minimal fix
(Approach 1: explicit `pending` short-circuits before the override), and closes
all open questions with evidence (the sole-writer grep).

Task coverage is complete: T-1 implements the guard, T-2 adds a regression test
that reproduces the ORC-117 failure case, T-3/fix-1 act as a phase gate with a
scope-correct verify command. No acceptance criteria are unaddressed.

No findings that would cause implementation to fail or miss acceptance criteria.
Implementation may proceed.
