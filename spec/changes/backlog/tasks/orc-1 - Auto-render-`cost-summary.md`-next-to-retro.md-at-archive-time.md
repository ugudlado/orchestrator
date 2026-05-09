---
id: ORC-1
title: >-
  Cost reporting pipeline: cost-summary.md at archive + median delta + run-end
  tail
status: In Progress
assignee:
  - '@claude'
created_date: '2026-05-03 10:55'
updated_date: '2026-05-09 10:51'
labels:
  - slug-cost-summary-on-archive
  - feature
  - score-9.2
  - recurrence-1
dependencies: []
references:
  - >-
    https://linear.app/home-labs-experiments/issue/HL-302/auto-render-cost-summarymd-next-to-retromd-at-archive-time
  - >-
    https://linear.app/home-labs-experiments/issue/HL-290/duckdb-cost-report-generator-complete-phase-integration
priority: high
ordinal: 34000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Consolidates ORC-1, ORC-2, and ORC-3 into a single deliverable.

cost-report.sh already works and produces a full 8-section report to stdout.
The orchestrate skill already calls it at workflow completion. Three gaps remain:

1. cost-summary.md not written to archive — archive-completed-change.sh never captures cost-report.sh stdout to a file
2. No median delta — no per-repo baseline view to compare this run's cost against the repo median
3. No run-end tail line — cost-report.sh has no --tail mode for a single summary line

## Scope

A. Add 3 lines to archive-completed-change.sh: call cost-report.sh, write output to $DST/cost-summary.md
B. Add feature_baseline DuckDB view (window median of cost_usd partitioned by repo_root)
C. Add --tail flag to cost-report.sh: single line "orc-39: $50.79 · 131m · 34 steps · 2.9x median"
D. Add median delta section to cost-report.sh formatter (reuses feature_baseline view)
E. Wire tail line into orchestrate skill SKILL.md run-end block (after full report)
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 New SQL query (or named query in `metrics-query.sh`): `feature-summary <change_id>` returning the full row + per-step rollup.
- [x] #2 New template at `config/templates/feature/cost-summary.md` with sections for totals, per-agent, per-phase, top-3 steps.
- [x] #3 Edit `config/steps/archive-completed-change.yaml` (or `mark-change-completed.yaml`) to render the template into the archive directory.
- [x] #4 Test: archive a small completed feature and assert `cost-summary.md` exists with non-empty sections.
- [x] #5 archive-completed-change.sh calls cost-report.sh and writes cost-summary.md to archive dir
- [ ] #6 feature_baseline view exists in metrics.duckdb with per-repo median cost_usd
- [x] #7 cost-report.sh --tail mode prints single summary line with cost, duration, steps, and Nx median
- [ ] #8 cost-report.sh full report includes a Median Delta section showing Nx repo median
- [ ] #9 skills/orchestrate/SKILL.md run-end block emits tail line after full cost report
- [ ] #10 tests: archive a completed feature and assert cost-summary.md exists with non-empty Executive Summary
- [ ] #11 ORC-2 and ORC-3 backlog tasks archived/marked Done
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented all 4 pieces:
- 0004_feature_baseline_view.sql migration: MEDIAN() window function partitioned by repo_root
- cost-report.sh: added --tail flag (single line) + Median Delta section in full report
- archive-completed-change.sh: calls cost-report.sh and writes cost-summary.md before git commit
- SKILL.md: run-end block now emits tail headline + full report
All 365 tests passing.
<!-- SECTION:NOTES:END -->
