---
id: ORC-44
title: 'HL-292: Standardize tool_calls key naming between skill prose and usage dict'
status: In Progress
assignee: []
created_date: '2026-05-08 12:05'
updated_date: '2026-05-10 11:31'
labels:
  - bug
  - orchestrator
dependencies: []
references:
  - >-
    https://linear.app/home-labs-experiments/issue/HL-292/standardize-tool-calls-key-naming-between-skill-prose-and-usage-dict
priority: low
ordinal: 2000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Skill prose in SKILL.md instructed LLM to record tools: {ToolName: count} in step_history, but canonical convention is usage.tool_calls per otel_map.py. This drift caused tool_calls DuckDB table fan-out to silently produce empty rows. Align all skill prose and usage dict schemas to use tool_calls consistently.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 All SKILL.md files use usage.tool_calls not tools: {}
- [ ] #2 DuckDB tool_calls table fan-out produces non-empty rows after a workflow run
- [ ] #3 otel_map.py and skill prose agree on key name
<!-- AC:END -->
