---
id: ORC-11
title: Step Timing Telemetry
status: Done
assignee: []
created_date: '2026-05-03 10:55'
updated_date: '2026-05-09 10:54'
labels:
  - slug-step-timing-telemetry
  - feature
  - score-7.7
  - recurrence-1
dependencies: []
priority: medium
ordinal: 10000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
<!-- migrated from spec/changes/backlog.md slug: step-timing-telemetry -->

**Original score:** 7.7 | **Recurrence:** 1

## Idea

Add wall-clock timing to every step execution by recording `started_at` and `completed_at` in each `step_history` entry. The grammar already declares these fields as optional in `step_record`, but nothing produces them today. With timing data, the `/telemetry` skill can show a Gantt-style phase breakdown, and `/learn` can flag duration outliers (the learn skill already references "steps taking >2x average" but has no data to work with).

## Why Now

The SWE metrics system (`swe_metrics.wall_clock_minutes`) already tracks total elapsed time, but it's a single number -- you can't tell whether the specify phase took 80% of the time or the implement phase did. The `step_record` grammar already has `started_at` and `completed_at` fields defined. The recent refactoring to consolidate state into `WORKFLOW_STATE_DIR` means there's exactly one place to read/write this data.

## Prototype

No visual prototype needed. The change is structural: update the orchestrate skill's dispatch loop (SKILL.md step 4) to emit timestamps in `step_history` entries, and update the telemetry skill to render a per-step duration breakdown.

## Priority

- User value: 8/10
- Strategic fit: 7/10
- Technical leverage: 8/10
- Effort: small
- **Score: 7.7**

---
<!-- SECTION:DESCRIPTION:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Superseded. step_events already has started_at, ended_at, duration_ms — captured by orchestrator done. phase_report aggregates duration per phase. Timing data fully covered by existing DuckDB schema.
<!-- SECTION:FINAL_SUMMARY:END -->
