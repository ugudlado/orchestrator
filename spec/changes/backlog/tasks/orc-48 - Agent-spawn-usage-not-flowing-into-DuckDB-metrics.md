---
id: ORC-48
title: Agent spawn usage not flowing into DuckDB metrics
status: Backlog
assignee: []
created_date: '2026-05-09 21:41'
labels:
  - bugfix
dependencies: []
priority: medium
ordinal: 45000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
All steps in cost reports show as 'inline' agent with output_tokens=0 and model=__default__, even when agent spawns (discoverer, architect, developer, reviewer) ran and their usage blocks were passed to orchestrator done. Observed in orc-30: rework_ratio 100%, per-agent table has only inline rows despite 4 agent spawns.\n\nRoot cause area: the CLI or record.py is not writing agent identity or token counts to the metrics DB for non-inline steps. The orchestrator done payload includes the usage block correctly — the drop happens between done payload and DuckDB write.\n\nTells: output_tokens=0, model=__default__, agent=inline for all 17 steps in orc-30 despite discoverer (74k tokens), architect (44k), developer (34k), reviewer (33k) all running.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Agent steps in step_history show correct agent name (not 'inline') in per-agent metrics table
- [ ] #2 input_tokens and output_tokens are non-zero for agent steps in DuckDB
- [ ] #3 model field resolves to actual model ID (not __default__) for agent steps
- [ ] #4 rework_ratio computed correctly — not inflated by misattributed inline records
<!-- AC:END -->
