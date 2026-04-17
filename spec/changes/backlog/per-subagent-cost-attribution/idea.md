# Sub-Agent Cost Attribution

## Idea
Today, when an agent invokes the `Agent` tool to spawn a sub-agent, the sub-agent's tokens land in the **parent agent's** bucket in `per_agent_metrics`. You cannot tell how much of an architect's 416k-token bill was the architect itself vs. a sub-agent it spawned. At Opus pricing ($15/$75 per 1M) a single sub-agent call can dwarf its parent invisibly.

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

## Why Now
Reveals the single most mis-attributed cost in the stack. Enables "stop spawning Opus architect for trivial sub-tasks" decisions that can cut feature cost 30–50% on Agent-heavy features. Depends on fix-cost-usd (otherwise the sub-agent costs will also be zero).

## Priority
- User value: 8/10
- Strategic fit: 8/10
- Technical leverage: 8/10
- Effort: medium
- **Score: 8.0**
