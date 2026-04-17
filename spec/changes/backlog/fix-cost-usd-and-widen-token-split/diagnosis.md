# Diagnosis: cost_usd = 0 for all per_agent_metrics rows

## Symptoms

Every row in `per_agent_metrics.cost_usd` is `0.0`. All `/telemetry`, `/learn`, and cost-based queries return zero cost despite 1,721,990 total tokens ingested across 20 rows covering 5 agents (developer, architect, reviewer, discoverer, workflow-improver). Commit `8c07afb` claimed to seed an `agent_pricing` table for cost inference, but the metric remains zero.

## Reproduction Steps

1. Run `repro.sh` from this directory (requires `duckdb` on PATH):
   ```
   bash spec/changes/backlog/fix-cost-usd-and-widen-token-split/repro.sh
   ```
2. Observe `cost_usd = 0.0` for all agents in section 1.
3. Observe `zero_cost_rows = 20, total_rows = 20` in section 2.
4. Observe `failing_rows = 20` in section 3 (assertion: `total_tokens > 10000 AND cost_usd = 0`).

See `repro.out` for the captured failure output.

## Expected vs Actual

- **Expected**: `per_agent_metrics.cost_usd` reflects token-based cost derived from `agent_pricing` rates. E.g., architect with 416,337 total_tokens at $15/1M input should show ~$6.24 or more.
- **Actual**: `per_agent_metrics.cost_usd = 0.0` for 100% of rows (20/20), regardless of token count.

## Investigation

### Evidence Gathered

- Live DuckDB query confirms 20/20 rows have `cost_usd = 0.0` with `total_tokens` ranging from 5,000 to 459,326 (see `repro.out` section 1–3).
- `agent_pricing` table exists and is fully seeded with prices for all 8 agents (see `repro.out` section 5), but no script joins against it at aggregation time.
- Commit `8c07afb` diff modifies only `scripts/routes.yaml` — the `agent_pricing` CREATE TABLE and INSERT were performed ad hoc via duckdb CLI and are not reproducible from the codebase. `register-repo.sh` has no reference to `agent_pricing`.
- `config/scripts/compute-swe-metrics.sh` PER_AGENT_TOKENS awk block at lines 421–453 reads `cost_usd` from `step_history[].usage.cost_usd` and accumulates `cost[agent] += cost_usd` (line 427). When the field is absent, awk uses the reset value of 0.
- `config/scripts/compute-swe-metrics.sh` already has a per-feature fallback chain at lines 252–257 (`PROXY_COST_USD` → `MODEL_NET_USD`), but the per-agent PER_AGENT_TOKENS block has no equivalent fallback.

### Data Flow Trace

1. Orchestrator dispatch loop runs a native agent (e.g., `developer` via `native_sonnet`). The Agent tool writes to `step_history[].usage`: `total_tokens`, `tool_uses`, `duration_ms`. No `cost_usd` field is written — the dispatch contract explicitly allows omitting it for native agents (no LiteLLM proxy lookup occurs).

2. `compute-swe-metrics.sh` is invoked on the state.yaml after agent completion. The PER_AGENT_TOKENS awk block (lines 421–453) walks `step_history` entries. At line 438, it reads: `in_history && in_usage && /^[[:space:]]+cost_usd:/ { gsub(/.*cost_usd: */, ""); cost_usd=$0+0 }`. When `cost_usd` is absent, `cost_usd` remains at its reset value of `0` (line 432).

3. At line 427, the block accumulates `cost[agent] += cost_usd` — summing zeros. The awk END block (line 447) emits `"cost_usd":0.000000` for every agent.

4. This JSON is written into the metrics YAML as `per_agent_tokens`. `register-repo.sh` (line 269) reads `per_agent_tokens[agent].cost_usd` and inserts it verbatim into `per_agent_metrics.cost_usd`.

5. Every downstream query (metrics-query.sh, `/telemetry`, `/learn`) reads `per_agent_metrics.cost_usd` and returns zeros.

## Root Cause

The PER_AGENT_TOKENS awk block in `config/scripts/compute-swe-metrics.sh` at line 427 (`cost[agent] += cost_usd`) sums zeros because native Agent-tool step_history entries omit the `cost_usd` field. The block has no fallback to derive cost from `total_tokens` × pricing rates. The `agent_pricing` DuckDB table exists and is seeded but is never joined at aggregation time.

Reference: `config/scripts/compute-swe-metrics.sh:427` (accumulator) and `config/scripts/compute-swe-metrics.sh:438` (field reader that finds nothing).

**Secondary finding**: The `agent_pricing` table schema and seed data are not defined in any script — commit `8c07afb` created them ad hoc via duckdb CLI. Re-provisioning the database from scratch would lose the pricing table entirely.

**Proposed approach**: After the awk PER_AGENT_TOKENS pass, for each agent where `cost[agent] = 0` and `tok[agent] > 0`, query `agent_pricing` via duckdb to derive `cost_usd = total_tokens × input_per_1m / 1000000` (conservative lower-bound approximation). Additionally, add `agent_pricing` CREATE TABLE + INSERT to `register-repo.sh` so it survives DB re-creation.

## Impact

### Severity

critical

### Affected Areas

- `per_agent_metrics.cost_usd` — 100% of ingested rows are zero (20/20).
- `/telemetry` cost reports — all zeros.
- `/learn` cost-based agent selection — broken (cannot rank by cost).
- Regression detection — cost threshold checks always pass (zero is never over budget).
- Per-subagent cost attribution — impossible.
- `backfill-zero-cost-metrics.sh` — script exists but the root cause means backfill without a join would also produce zeros.

**Other aggregators with the same pattern**: `config/scripts/compute-swe-metrics.sh` lines 193–219 (STATE_USAGE awk block) also sums `cost_usd` from step_history with no fallback. `PROXY_COST_USD` from that block feeds the per-feature `NET_USD` fallback at lines 252–257, which falls through to `MODEL_NET_USD` (token × hardcoded rate). However, that fallback uses `MODEL` from JSONL enrichment — which only works when session JSONL files are available. If JSONL is absent, `MODEL` is "unknown" and `get_pricing("unknown")` returns the default (opus) rate.

### Since When

Introduced when all agents were switched to native_sonnet in commit `8c07afb` (2026-04-18). Prior to that commit, agents routed via qwen proxy through LiteLLM, which did write `cost_usd` to step_history. The PER_AGENT_TOKENS block worked correctly then. The routing change broke cost tracking without a corresponding fix to the aggregator.

## Unresolved Questions

1. **Pricing formula with only total_tokens**: `agent_pricing` has `input_per_1m`, `output_per_1m`, `cache_read_per_1m` but step_history only has `total_tokens` (no input/output/cache split). The fix must choose a formula. Options: (a) `total_tokens × input_per_1m / 1000000` as a conservative lower bound; (b) blended rate (e.g., 80% input + 20% output approximation). Option (a) is recommended for this fix iteration — it is explicit and underestimates rather than overestimates, with the note that widening to per-category tokens is deferred per idea.md.

2. **Re-ingest strategy**: Existing 20 ingested rows have `cost_usd=0`. After the fix, historical state.yamls must be re-ingested via `register-repo.sh` for metrics to be accurate. No automated re-ingest trigger exists today — a manual `register-repo.sh --rebuild` is required.

3. **Per-feature fallback unification**: Lines 252–257 already compute `NET_USD` from `PROXY_COST_USD` with a fallback to `MODEL_NET_USD` for the feature-level cost. The per-agent PER_AGENT_TOKENS block independently re-sums raw `cost_usd` fields without mirroring this fallback. The two paths should eventually be reconciled, but unification is out of scope for this fix — the per-agent block fix is the minimal correct change.

## Linear Ticket

none

<!-- Format contract: contracts/artifact-formats.md § Diagnosis Format Contract -->
