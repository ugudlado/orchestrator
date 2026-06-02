---
feature-id: orc-119
review-verdict: pass
---

# Design Review: ORC-119 DAG Walker on_failure Routing Bug

## Verdict: PASS

Overall score: **10**

## Dimension Scores

| Dimension      | Score | Notes |
|----------------|-------|-------|
| completeness   | 10    | All required sections present and substantive. Decisions closes both OQs. |
| ac_coverage    | 10    | AC-1–AC-5 all mapped in task `why` fields. No orphaned tasks. |
| task_quality   | 10    | T-1 is XS and independently verifiable. T-2 correctly depends on T-1 (green-from-first-run, no xfail needed). T-3 is a valid phase gate. |
| feasibility    | 10    | Source code verified: `readiness.py:76-83` and `record.py:1287` match design's claimed lines exactly. OQ-2 grep claim confirmed. Change is 2 lines in one file. |
| scope_control  | 10    | Non-Goals explicitly exclude `_resolve_routing`, retry-cap, and step_history mutation. All tasks stay within stated scope. |

## Summary

The design is exceptionally thorough for an XS change. The root cause is precisely identified, verified against the live codebase, and the fix is minimal and correct. The two-approach comparison is honest (Approach 2's step_history mutation risk is correctly called out). AC coverage is complete, and the T-2 regression test correctly targets the exact fixture gap that allowed the bug to go unnoticed (the existing `_nodes_state` fixture lacked a completed step_history entry for the reset target).

No findings. Implementation may proceed.
