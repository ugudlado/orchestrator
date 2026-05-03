---
id: ORC-20
title: Step Contract Input/Output Dependency Graph
status: To Do
assignee: []
created_date: '2026-05-03 10:55'
updated_date: '2026-05-03 11:00'
labels:
  - slug-step-contract-input-output-graph
  - feature
  - score-5.5
  - recurrence-1
dependencies: []
priority: low
ordinal: 19000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
<!-- migrated from spec/changes/backlog.md slug: step-contract-input-output-graph -->

**Original score:** 5.5 | **Recurrence:** 1

## Idea

Each step contract declares `inputs:` and `outputs:`. These form an implicit dependency graph: `explore` outputs `discovery_result`, which `create-or-refresh-artifacts` consumes via `phase_context_bundle`. But this graph is never materialized or validated. Build a script or skill that: (1) parses all step contract YAMLs and extracts inputs/outputs, (2) for each schema, walks the phase step lists and verifies that every step's declared inputs are satisfied by a prior step's outputs or by initial state, (3) detects orphaned outputs (produced but never consumed) and unresolved inputs (consumed but never produced). Output as a dependency graph (text or diagram). This would catch wiring bugs like a schema that skips `explore` but still expects `discovery_result` downstream.

## Why Now

With 38 step contracts and 6 schemas, manual tracking of which step produces what and which step needs what is error-prone. The conditional step system (`if design`, `if ux_design`) makes this worse -- a step might be skipped by a flag but its output might still be expected downstream. This idea complements `schema-validation-step` (which validates at load time) by providing an offline analysis tool.

## Priority

- User value: 5/10
- Strategic fit: 7/10
- Technical leverage: 6/10
- Effort: medium
- **Score: 5.5**

---
<!-- SECTION:DESCRIPTION:END -->
