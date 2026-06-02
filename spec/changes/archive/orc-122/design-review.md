# Design Review — ORC-122: orchestrator graph cost/token/attempt overlay

## Verdict: PASS

Overall score: **9 / 10**

| Dimension | Score | Notes |
|-----------|-------|-------|
| completeness | 10 | All sections present and substantive; Verified System Boundaries section is above-average due to pre-proving join keys against real run data (orc-120, orc-99, orc-74, orc-118). |
| ac_coverage | 10 | Every AC has at least one task with explicit `why` tracing; AC-5 (HTML) covered by T-3; all five ACs have end-to-end coverage through T-4 integration gate. |
| task_quality | 9 | RED-phase xfail annotation is correctly specified in T-1 `change:` field; T-4 removes markers with a guard command; T-3 smoke script asserts step_data payload contents rather than the always-present `STEP_DATA` constant. Minor: T-2 `change:` leaves xfail cleanup to "only if xpass-strict issue," which is technically correct (strict=False allows xpass) but adds a clarification burden — T-4 is the unambiguous gate. Not a critical or important finding. |
| feasibility | 10 | No DuckDB dependency, no workflow-report touches, no new CLI flags — all constraints honored. Slug-exists guard (`_resolve_slug_state` → exit 3) preserved. `os.path.dirname(state_yaml_path)` is the correct derivation for `state_dir`. Duplication vs. shared module decision is well-reasoned for two call sites. |
| scope_control | 10 | Non-Goals are explicit and directly address over-reach (duration, status coloring, new flags). Intentional capability change (render_graph losing CLI entry point) is documented in Decisions rather than treated as a silent side effect. |

## Summary

The design is thorough and implementation-ready. The Verified System Boundaries section derisk the implementation by confirming join keys, state file shapes, and multi-state-file aggregation against real run data before the design was finalized. Task sequencing (RED → GREEN → CLI wiring → integration gate) is sound. xfail annotation discipline is correctly applied — T-1 mandates it, T-4 removes it with a guard. No structural gaps that would cause implementation to fail or miss ACs.
