# Design Review: orc-125 — Restore mid-run live cost display

**Verdict:** pass
**Overall:** 9 (minimum across dimensions)

## Scores

| Dimension      | Score | Notes |
| -------------- | ----- | ----- |
| completeness   | 10    | Context, Goals, Non-Goals, three Approaches Considered with pros/cons, Selected Approach with heuristic rationale, High/Low-Level Design, Constraints, Trade-offs, and a 5-item Acceptance Criteria section are all present and non-empty. A "Verified System Boundaries" section grounds every call-site claim. |
| ac_coverage    | 9     | All 5 ACs are covered by tasks: AC-1/AC-2/AC-5 → T-1+T-2, AC-1/AC-2/AC-3 → T-3, AC-2/AC-4 → T-4. Every task carries a `why` field tracing to specific ACs. Every discovery UC (UC-1, UC-2, UC-E1, UC-E2) is traced by at least one AC. |
| task_quality   | 9     | Four small, ordered, independently verifiable tasks; each has repo-root-relative `verify` commands. The RED test task (T-1) explicitly instructs `@pytest.mark.xfail(strict=False)` in its `change:` field (critical TDD check satisfied), and T-2/T-4 remove those markers. No task touches unrelated files. Phase-gate (T-4) verify is scoped to the feature test file per the phase-gate-verify learning rather than the full suite. |
| feasibility    | 9     | Selected approach is consistent with discovery constraints (state.yaml source, no DB). All caller-site claims (record.py:493-497, bin/orchestrator:352/382, run_loop.py:515, workflow_report_step.py:77) were grep-verified against HEAD and recorded in "Verified System Boundaries". No blocking open questions remain (OQ-1/OQ-2 resolved in Decisions). |
| scope_control  | 10    | Non-Goals are explicit and directly enforce the ticket constraints: no DuckDB/metrics.duckdb/upsert.py, no new CLI subcommand, no cross-file roll-up, no schema/step edits. No task implements anything outside the stated Goals; AC-4 + T-4 actively guard the agent-agnostic and no-DuckDB boundaries. |

## Summary

The design cleanly re-derives the running cost total from `step_history[].usage.cost_usd`
in the in-progress `state.yaml`, reusing the exact field `record.py` already writes and the
summation pattern `workflow-report` already uses. It restores ORC-42's `estimated_cost_so_far`
action-dict contract for the remote/DRIVE.md + orchestrate path and adds a local `run_loop`
meter, satisfying AC-1 ("orchestrator next or equivalent"). The DuckDB prohibition (AC-2),
mid-run requirement (AC-3), and agent-agnostic constraint (AC-4) are each pinned to a concrete
verify command. Task ordering keeps pytest green at every commit boundary via explicit xfail
handling. No critical or important findings.

## Findings

None blocking. (Non-blocking observation, not scored against the design: T-3's note to
"grep tests for emit_json/estimated_cost" correctly anticipates that adding a key to the
action dict may require updating any test that pins the exact emitted JSON — the design already
flags this in Constraints.)
