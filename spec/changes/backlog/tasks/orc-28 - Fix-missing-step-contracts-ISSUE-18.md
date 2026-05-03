---
id: ORC-28
title: Fix missing step contracts (ISSUE-18)
status: To Do
assignee: []
created_date: '2026-05-03 10:56'
updated_date: '2026-05-03 11:00'
labels:
  - slug-fix-missing-step-contracts
  - bug
  - score-7.8
  - recurrence-1
dependencies: []
priority: medium
ordinal: 27000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
<!-- migrated from spec/changes/backlog.md slug: fix-missing-step-contracts -->

**Original score:** 7.8 | **Recurrence:** 1

## Idea

workflow-init validates every schema-declared step against `$ORCHESTRATOR_HOME/config/steps/<id>.yaml` at workflow start. Missing contracts get pre-filtered into `workflow_plan.<phase>.filtered` with reason `"contract file missing (no config/steps/<id>.yaml)"` and a single WARNING is emitted. Never fails init. A stricter sibling: `orchestrator doctor` lists orphan schema refs as a deep-check item.

## Why Now

The bugfix schema declares `run-simplify` and `run-feature-verification` but neither contract file exists. Autopilot run 2026-04-19-003 hit this twice, once per phase. Each hit required a manual edit to state.yaml to move the missing step to the filtered list — otherwise `orchestrator next` errors out mid-dispatch. Happens on every bugfix run.

## Prototype

```
workflow-init start
  schema: bugfix
  declared steps: 13
  resolved contracts: 11
  [WARN] 2 steps declared without contracts — pre-filtered
    - run-simplify (reason: no config/steps/run-simplify.yaml)
    - run-feature-verification (reason: no config/steps/run-feature-verification.yaml)
  workflow_plan written
```

## Priority

- User value: 8/10 (every bugfix run currently needs a manual workaround)
- Strategic fit: 8/10 (infrastructure hygiene; fits the doctor/validate theme)
- Technical leverage: 7/10 (tiny change, ripple benefit across all schemas)
- Effort: small
- **Score: 7.8**

## Source

spec/changes/archive/2026-04-19-live-telemetry-and-repeat-until-enforcement/retro.md §ISSUE-18

---
<!-- SECTION:DESCRIPTION:END -->
