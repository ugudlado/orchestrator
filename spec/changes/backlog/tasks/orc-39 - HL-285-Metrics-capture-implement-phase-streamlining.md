---
id: ORC-39
title: 'HL-285: Metrics capture + implement-phase streamlining'
status: Done
assignee: []
created_date: '2026-05-08 12:04'
updated_date: '2026-05-09 10:23'
labels:
  - feature
  - orchestrator
dependencies: []
references:
  - >-
    https://linear.app/home-labs-experiments/issue/HL-285/metrics-capture-implement-phase-streamlining
priority: high
ordinal: 36000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Fix three tightly-coupled workflow defects: (1) Zero-cost metrics — compute-swe-metrics.sh runs before archive-completed-change writes completed_at, causing parse_session_jsonl to produce zeros. (2) Per-agent/per-step metrics undercounted — inline steps never record a usage: block. (3) Simplify + learn steps oversized — routinely consume 30-40% of feature cost for minimal value. HL-283 and HL-285 are duplicates; this task tracks the canonical work.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 compute-swe-metrics.sh runs after completed_at is written, producing non-zero cost
- [ ] #2 Inline steps record usage blocks in state.yaml step_history
- [ ] #3 Simplify and learn step token consumption is right-sized
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Fixed three metrics-capture defects confirmed by diagnosis:

- D1b (Phase 5 subagent row timing): extended record.py Phase 5 to call _write_subagent_events and _write_driver_session inside the mark-change-completed transaction, so compute-swe-metrics sees correct turns before remove-worktree fires.
- D2b (agent misattribution): added _resolve_contract_agent helper in record.py that looks up the declared agent from the step contract and rewrites inline self-reports, emitting a stderr warning on rewrite.
- D3 (learn/simplify no gate): registered gates.learn and behavioral.simplify in flags.yaml with --no-learn/--no-simplify CLI flags; gated the FINAL-TASK SIMPLIFY PASS in execute-next-task.yaml on flags.simplify.

Also landed two workflow fixes from the learn cycle: ux_design gate registered in flags.yaml (was silently missing), run-ux-critique output keys de-annotated (type annotations were being serialized as literal string keys).

Tests: 361 baseline → 371 passing, 0 failing. Phase review: 9/10. Cost: $11.36 implementation ($50.79 total including driver session).
<!-- SECTION:FINAL_SUMMARY:END -->
