# Fix cost_usd Inference + Widen Token Split

## Idea
Two coupled problems with one root cause: (1) `per_agent_metrics.cost_usd` is NULL or 0 for every ingested feature despite `agent_pricing` being seeded, and (2) metrics tables only store `total_tokens` — no split into input/output/cache_read/cache_creation — but `agent_pricing` has a 5–50× cost spread across those categories, so cost math can't be correct even in principle.

## Root Cause (from diagnosis 2026-04-18)
`compute-swe-metrics.sh` (PER_AGENT_TOKENS awk block) reads `cost_usd` directly from each `step_history[].usage.cost_usd` field and sums. Native Agent-tool calls only write `total_tokens`, not `cost_usd` — the orchestrate dispatch loop contract explicitly allows omitting `cost_usd` for native agents. Result: aggregator sums zeros, writes `cost_usd: 0.000000` into `per_agent_tokens`, which propagates to `per_agent_metrics.cost_usd` at ingest.

## Why Now
Every downstream report (`/telemetry`, `/learn`, proposed regression detection, cost-based agent selection) is reading zeros. Commit 8c07afb claimed to seed `agent_pricing` for cost inference but no script joins against it. This is the load-bearing metric for the whole observability stack.

## Fix
1. In `compute-swe-metrics.sh`: when a step has tokens but no `cost_usd`, compute from `agent_pricing` by joining on agent name and model.
2. Widen `step_history.usage`, `per_step_metrics`, `per_agent_metrics` with `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`. JSONL enrichment already has these — stop flattening at ingest.
3. Add post-ingest assertion: `total_tokens > 10000 AND cost_usd = 0` must fail.
4. Add `cache_hit_rate` derived view.

## Priority
- User value: 10/10 (unblocks every other metric)
- Strategic fit: 10/10
- Technical leverage: 9/10
- Effort: small-medium
- **Score: 9.5**
