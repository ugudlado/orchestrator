---
id: ORC-43
title: 'HL-291: Feature complexity tracking + cost-per-complexity reporting'
status: Backlog
assignee: []
created_date: '2026-05-08 12:05'
labels:
  - orchestrator
dependencies: []
references:
  - >-
    https://linear.app/home-labs-experiments/issue/HL-291/feature-complexity-tracking-cost-per-complexity-reporting
priority: low
ordinal: 40000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Follow-up to DuckDB cost report generator (HL-290/ORC-1). Add explicit complexity field (XS/S/M/L/XL) to feature records in DuckDB. Compute cost-per-complexity metrics and surface them in the cost report. Goal: normalize cost comparisons across features by complexity tier.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Complexity field (XS/S/M/L/XL) captured in DuckDB features table
- [ ] #2 cost-per-complexity breakdown appears in cost-report.md
- [ ] #3 orchestrator next prompts for complexity at workflow-init or reads from state.yaml
<!-- AC:END -->
