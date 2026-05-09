---
id: ORC-46
title: 'HL-295: Per-step allowed_tools enforcement + tool-use attribution'
status: Backlog
assignee: []
created_date: '2026-05-08 12:05'
labels:
  - orchestrator
dependencies: []
references:
  - >-
    https://linear.app/home-labs-experiments/issue/HL-295/per-step-allowed_tools-enforcement-tool-use-attribution
priority: low
ordinal: 43000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Agent roles declare allowed tools in frontmatter today; step contracts don't. This means every step invocation gets the full tool list for the role, even when the step only needs a subset. Add allowed_tools to step contracts and enforce them at dispatch time. Enables per-step tool-use attribution in metrics.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Step contracts support an allowed_tools field
- [ ] #2 Dispatcher enforces per-step tool restrictions at invocation time
- [ ] #3 Tool-use attribution in step_events is segmented by step, not just role
<!-- AC:END -->
