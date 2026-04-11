---
feature-id: track-per-agent-token-attribution
linear-ticket: HL-273
---

# Chore: Track per-agent token attribution

## What

Add a `usage` field to the `step_history` entry format so that each agent-spawned
step records its token consumption. Update the dispatch loop convention to capture
the Agent tool's usage metadata (total_tokens, tool_uses, duration_ms) after each
agent spawn and write it to the step_history entry. Update compute-swe-metrics to
aggregate per-agent token attribution from step_history into the JSONL output.

Files affected:
- `config/steps/CONVENTIONS.md` -- add `usage` to step_history entry format and State Field Registry
- `config/steps/compute-swe-metrics.yaml` -- add per-agent token aggregation to instruction
- `config/scripts/compute-swe-metrics.sh` -- add per-agent extraction from state.yaml step_history

## Why

Token cost is currently tracked only at the aggregate level (total tokens per feature
in the JSONL). Without per-agent attribution, there's no way to know which steps are
expensive (architect vs developer vs reviewer) or to validate that model routing
decisions (like HL-270's Sonnet for simplifier) are actually saving tokens. Per-agent
attribution enables data-driven cost optimization.

## Acceptance Criteria

- [ ] AC-1: CONVENTIONS.md step_history entry format includes an optional `usage` field
  with structure `{ total_tokens, tool_uses, duration_ms }`.
- [ ] AC-2: CONVENTIONS.md State Field Registry includes `step_history[].usage` with
  format documentation.
- [ ] AC-3: compute-swe-metrics.yaml instruction references extracting per-agent token
  data from step_history entries.
- [ ] AC-4: compute-swe-metrics.sh extracts usage data from step_history and includes
  a `per_agent_tokens` object in the JSONL output with per-agent-type totals.
- [ ] AC-5: Existing step_history entries without `usage` field are handled gracefully
  (backward compatible — missing usage treated as unknown/zero).
