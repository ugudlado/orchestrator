---
id: ORC-42
title: 'HL-289: Live per-step cost probe in orchestrator next action dict'
status: Done
assignee: []
created_date: '2026-05-08 12:05'
updated_date: '2026-05-09 14:32'
labels:
  - orchestrator
dependencies: []
references:
  - >-
    https://linear.app/home-labs-experiments/issue/HL-289/live-per-step-cost-probe-in-orchestrator-next-action-dict
priority: low
ordinal: 39000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Add estimated_cost_so_far to the action dict from 'orchestrator next', summed from DuckDB step_events.cost_usd for the current change_id. Gives the user a running cost display between steps. Child of HL-287 / ORC-4 dogfood work.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 orchestrator next action dict includes estimated_cost_so_far field
- [ ] #2 Value is summed from step_events.cost_usd in DuckDB for current change_id
- [ ] #3 Cost is visible to user between steps in the orchestrate loop
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
cost_so_far was already implemented: bin/orchestrator line 243 adds it to every action dict, summed from step_events.cost_usd via sum_cost_usd() in upsert.py.

AC-1 ✓ — action dict always includes cost_so_far field (0.0 when DB unavailable)
AC-2 ✓ — value is SUM(cost_usd) FROM step_events WHERE repo_root=? AND change_id=?
AC-3 ✓ — wired into orchestrate SKILL.md dispatch loop: prints "[cost so far: $X.XX]" after every orchestrator next call when cost_so_far > 0

Live verification: orchestrator next on orc-39 archive returns cost_so_far: 50.79.
<!-- SECTION:FINAL_SUMMARY:END -->
