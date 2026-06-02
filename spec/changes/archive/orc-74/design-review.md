# Design Review: ORC-74 — Split record.py god module

**Verdict:** PASS  
**Overall score:** 9

## Dimension Scores

| Dimension | Score | Notes |
|-----------|-------|-------|
| completeness | 10 | Goals, Non-Goals, 3 approaches with pros/cons, Selected Approach with tiebreak, 5 ACs, resolved Open Questions — all present and substantive |
| ac_coverage | 9 | All 5 design ACs trace to tasks (T-1→AC-2,AC-4; T-2→AC-3,AC-4; T-3→AC-1,AC-5). Minor clarity: T-3's `why` cites "AC-1 and AC-5" but AC-1 is consciously not met in full per Non-Goals — not a structural gap, but a reader could be confused |
| task_quality | 10 | T-1 and T-2 are independently runnable on disjoint record.py regions; each has import-check + targeted pytest verify commands that exit 0 post-move. T-3 is a pure phase gate with no code edits. No TDD RED tests; no xfail concerns |
| feasibility | 9 | Approach mirrors existing pricing.py re-export precedent (ORC-71); one-way dependency preserved; TYPE_CHECKING guard avoids runtime cycle; REPEAT_PREDICATES kept in record.py to preserve readiness.py lazy-import. Minor: T-1 and T-2 both modify record.py and are marked independent — if run in parallel, edits could conflict. Serialized execution (default) is safe |
| scope_control | 10 | Non-Goals are explicit and well-reasoned; no task touches routing/boundary/next-step/REPEAT_PREDICATES; no test file edits permitted |

## Summary

The design is sound. The two-module extraction (metrics.py + payload.py) with re-exports is the right call: it mirrors the established ORC-71 pricing.py pattern, maintains one-way dependency direction, preserves all 14 test files' import paths without modification, and stays within discoverable complexity. The conscious descope of AC#1/AC#3 (record.py at ≈860 LOC, not ≤500) is justified — routing/boundary/next-step are cohesive with the in-place `state_raw` mutation transaction, and extracting them would add code rather than remove it. The tasks are small, independently verifiable, and tightly scoped.

No critical or important findings. The two minor observations above (AC-1 clarity in T-3's `why`, parallel-edit risk in record.py) do not block implementation.
