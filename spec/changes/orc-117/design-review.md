# Design Review: ORC-117 — Remove flags system from codebase

**Verdict:** PASS  
**Overall score:** 9 / 10

## Dimension Scores

| Dimension      | Score | Notes |
|----------------|-------|-------|
| completeness   | 10    | All required sections present and non-empty |
| ac_coverage    | 10    | Every AC maps to ≥1 task; every task has a `why` tracing to AC(s) |
| task_quality   | 10    | Tasks are small, focused, and independently verifiable |
| feasibility    | 9     | Design is internally consistent; minor note below |
| scope_control  | 10    | Non-Goals are explicit; no task drifts outside stated Goals |

## Summary

The design is well-scoped and thorough. The three-file producer→state→consumer chain is correctly identified, and the deletion plan covers all three links with precise line-level guidance. The AC-to-task mapping is complete and bidirectional. Task ordering (T-3 depends on T-1; T-4 depends on T-1+T-2; T-5 gates all) reflects actual dependency structure.

The Non-Goals section correctly excludes `resolved_flags` fixtures, the `" if <flag>"` suffix-stripping, and a standalone rule-merge doc — keeping the scope tight.

## Findings

**Feasibility — minor (no score cap)**

T-2's `verify` command (`python seed_parse_overrides.py x feature . config/workflows/feature.yaml`) exercises the new override-rejection path, which is correct. However, it does not verify the happy path for `seed_write_state.py` (that it writes state without a `flags` key). This is covered by T-5's full suite run, so there is no gap in AC coverage — it is a completeness note only, not a blocking finding. The full suite gate (T-5) is sufficient.

## No Critical or Blocking Findings

Implementation may proceed.
