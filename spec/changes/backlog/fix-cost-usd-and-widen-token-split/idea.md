# Fix cost_usd Inference + Widen Token Split

## Idea
Two coupled problems with one root cause: (1) `per_agent_metrics.cost_usd` is NULL or 0 for every ingested feature despite `agent_pricing` being seeded, and (2) metrics tables only store `total_tokens` — no split into input/output/cache_read/cache_creation — but `agent_pricing` has a 5–50× cost spread across those categories, so cost math can't be correct even in principle.

## Root Cause (from diagnosis 2026-04-18)
`compute-swe-metrics.sh` (PER_AGENT_TOKENS awk block) reads `cost_usd` directly from each `step_history[].usage.cost_usd` field and sums. Native Agent-tool calls only write `total_tokens`, not `cost_usd` — the orchestrate dispatch loop contract explicitly allows omitting `cost_usd` for native agents. Result: aggregator sums zeros, writes `cost_usd: 0.000000` into `per_agent_tokens`, which propagates to `per_agent_metrics.cost_usd` at ingest.

## Why Now
Every downstream report (`/telemetry`, `/learn`, proposed regression detection, cost-based agent selection) is reading zeros. Commit 8c07afb claimed to seed `agent_pricing` for cost inference but no script joins against it. This is the load-bearing metric for the whole observability stack.

## Fix (this iteration — descoped per diagnosis)
1. In `compute-swe-metrics.sh`: when a step has tokens but no `cost_usd`, compute from `agent_pricing` by joining on agent name and model.
2. Add post-ingest assertion: `total_tokens > 10000 AND cost_usd = 0` must fail.
3. Verify with `SELECT COUNT(*) FROM per_agent_metrics WHERE cost_usd > 0` — goes from 0 to non-zero after re-ingest.

## Additional Scope (T-4 archive patches)

Five archived state.yaml files were directly patched to inject cost_usd into their per_agent_tokens JSON using the same formula the script fix applies (`total_tokens × input_per_1m / 1_000_000`). JSONL source data has aged out so a pipeline re-run would yield different totals; the stored total_tokens in these archives are authoritative. Affected archives:

- `spec/changes/archive/2026-04-17-cross-repo-metrics-duckdb/state.yaml`
- `spec/changes/archive/2026-04-17-duckdb-ingest-normalized-metrics-tables/state.yaml`
- `spec/changes/archive/2026-04-17-metrics-capture-and-workflow-streamlining/state.yaml`
- `spec/changes/archive/2026-04-11-hl-276/state.yaml`
- `spec/changes/archive/2026-04-12-hl-278/state.yaml`

## Out of scope (deferred — file separately if still needed after #1 lands)
- Widening `step_history.usage`, `per_step_metrics`, `per_agent_metrics` with `input_tokens` / `output_tokens` / `cache_read_tokens` / `cache_creation_tokens`. Re-evaluate after a token×price estimate restores cost_usd — the split may or may not be worth the schema migration at that point.
- `cache_hit_rate` derived view.

## Priority
- User value: 10/10 (unblocks every other metric)
- Strategic fit: 10/10
- Technical leverage: 9/10
- Effort: small-medium
- **Score: 9.5**
