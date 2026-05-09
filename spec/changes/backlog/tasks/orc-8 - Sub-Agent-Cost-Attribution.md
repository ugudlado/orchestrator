---
id: ORC-8
title: Sub-Agent Cost Attribution
status: To Do
assignee: []
created_date: '2026-05-03 10:55'
updated_date: '2026-05-08 12:04'
labels:
  - slug-per-subagent-cost-attribution
  - feature
  - score-8.0
  - recurrence-1
dependencies: []
references:
  - >-
    https://linear.app/home-labs-experiments/issue/HL-281/metrics-pipeline-underreports-cost-parse-all-agent-usage-blocks-right
priority: medium
ordinal: 7000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
<!-- migrated from spec/changes/backlog.md slug: per-subagent-cost-attribution -->

**Original score:** 8.0 | **Recurrence:** 1

## Idea

Today, when an agent invokes the `Agent` tool to spawn a sub-agent, the sub-agent's tokens land in the **parent agent's** bucket in `per_agent_metrics`. You cannot tell how much of an architect's 416k-token bill was the architect itself vs. a sub-agent it spawned. At Opus pricing ($15/$75 per 1M) a single sub-agent call can dwarf its parent invisibly.

## Why Now

Reveals the single most mis-attributed cost in the stack. Enables "stop spawning Opus architect for trivial sub-tasks" decisions that can cut feature cost 30–50% on Agent-heavy features. Depends on fix-cost-usd (otherwise the sub-agent costs will also be zero).

## Evidence

- `per_tool_uses` shows the `Agent` tool invoked 16 times across ingested features.
- No table tracks parent→subagent token flow. `per_agent_metrics` collapses everything under the caller.

## Fix

1. Parse `Agent` tool invocations from JSONL — each call has a `subagent_type` and the child session produces its own token counts.
2. New table `per_subagent_calls`:
   ```
   parent_change_id, parent_agent, subagent_type,
   input_tokens, output_tokens, cost_usd, duration_ms
   ```
3. Decision (design phase): either subtract sub-agent tokens from parent totals, or keep both clearly labeled (`self_tokens` vs `inclusive_tokens`).

## Priority

- User value: 8/10
- Strategic fit: 8/10
- Technical leverage: 8/10
- Effort: medium
- **Score: 8.0**

---
<!-- SECTION:DESCRIPTION:END -->
