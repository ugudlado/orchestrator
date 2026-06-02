---
feature-id: orc-118
review-date: 2026-06-02
verdict: pass
---

# Design Review: ORC-118 — Move Agent Step Execution into bin/orchestrator

## Verdict: PASS

Overall score: **9 / 10**

## Dimension Scores

| Dimension | Score | Notes |
|-----------|-------|-------|
| completeness | 10 | All required sections present and substantive: Goals, Non-Goals, Approaches Considered (3 with pros/cons), Selected Approach with rationale, 6 ACs, Constraints, Trade-offs, Decisions, Open Questions all resolved or deferred. |
| ac_coverage | 9 | All 6 ACs map to tasks; every task has a `why` tracing to an AC. T-2 verify references `tests/test_parse_completion.py` — file confirmed to exist. |
| task_quality | 9 | All 15 tasks have verify commands. RED/GREEN pairing is consistent. All RED tasks (`change:` fields for T-1, T-3, T-5, T-7, T-9, T-11) explicitly instruct `@pytest.mark.xfail(strict=False)` — satisfies the xfail annotation rule. T-13 documents the bats `skip` analog explicitly. T-15 strips xfail markers at the phase gate as required. |
| feasibility | 9 | Selected approach is consistent with discovery.md decisions. All OQs resolved. Behavior change (exit 4/5 → record-and-loop) is explicitly surfaced. `tests/test_run_workflow_smoke.bats` (referenced in T-14 verify) confirmed to exist. |
| scope_control | 10 | Non-Goals are explicit and comprehensive (no run-workflow.sh deletion, no config/contract/state format changes, no orchestrator complete, no duckdb, no DAG). No task exceeds Goal scope. |

## Summary

The design is complete and well-reasoned. The symmetry-completion framing (ORC-112 did inline scripts; ORC-118 does agent steps via the same delegation pattern) is coherent. The dependency chain (T-1→T-2→...→T-15) is appropriately linear for a sequential port.

Key risks are addressed:

- **Hyphen-import blocker** (`parse-completion.py`) handled by T-1/T-2 RED/GREEN promotion to `orchestrator_next/parse_completion.py`.
- **xfail annotations** — all RED tasks carry explicit `@pytest.mark.xfail(strict=False)` contracts in their `change:` field; the implement-tasks step will not encounter the verify-exit-0 contradiction that abandoned prior ORC-118 attempts.
- **Bats RED analog** — T-13's `skip` lines are documented; T-14 removes them before the grep assertions run.
- **Deliberate semantics change** (exit 4/5 → record-and-loop) is surfaced in Trade-offs and Decisions, not silent.
- **detect-workflow-issues.sh** correctly stays as a subprocess (shared with skills/orchestrate).

## Minor Observations (non-blocking)

- **T-10's detect-workflow-issues.sh path** via `config_root().parent / "scripts" / "lib"` is implicit. The implementation should resolve and verify this path before invoking the subprocess.
- **T-13 uses bats for a grep assertion.** Functional; a pytest-based grep check would stay within the canonical test runner but this is style preference only.

Neither observation affects AC coverage or implementation correctness. Implementation can proceed.
