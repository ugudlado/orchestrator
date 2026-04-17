# Fix Plan: cost_usd = 0 for all per_agent_metrics rows

## Fix Strategy

The fix has two parts:

**Part 1 — Cost inference in compute-swe-metrics.sh**: After the PER_AGENT_TOKENS awk block accumulates per-agent token totals, add a post-processing step that, for each agent where `cost_usd = 0` and `total_tokens > 0`, queries `agent_pricing` via `duckdb` to look up `input_per_1m` by agent name, then computes `cost_usd = total_tokens × input_per_1m / 1000000`. This is a conservative lower-bound approximation (uses only input rate) — acceptable until the token split widening is implemented (deferred per idea.md). The result replaces the zero in the PER_AGENT_TOKENS JSON before it is written to the metrics YAML.

**Part 2 — Durable agent_pricing schema in register-repo.sh**: Add the `CREATE TABLE IF NOT EXISTS agent_pricing` DDL and `INSERT OR REPLACE` seed statements to `register-repo.sh` so the table survives database re-creation. Currently the table was seeded ad hoc via duckdb CLI and is not reproducible from the codebase.

Root cause reference: `config/scripts/compute-swe-metrics.sh:427` — `cost[agent] += cost_usd` sums zeros because native agent step_history entries omit `cost_usd`. See `diagnosis.md` Root Cause section.

## Affected Files

- `config/scripts/compute-swe-metrics.sh:421-453` — add post-awk cost inference block: after PER_AGENT_TOKENS awk completes, for each agent with `total_tokens > 0` and `cost_usd = 0`, query `agent_pricing` via duckdb and inject inferred cost into the JSON before writing.
- `config/scripts/register-repo.sh:76-148` — add `CREATE TABLE IF NOT EXISTS agent_pricing (agent VARCHAR PRIMARY KEY, model VARCHAR, backend VARCHAR, input_per_1m DOUBLE, output_per_1m DOUBLE, cache_read_per_1m DOUBLE)` DDL and corresponding `INSERT OR REPLACE` seed rows (derived from `config/pricing.yaml` × `scripts/routes.yaml` agent→model mapping).
- `config/scripts/__tests__/` or `config/scripts/test-fixtures/` — add fixture state.yaml with at least one step_history entry where `total_tokens > 10000` and `cost_usd` is absent, to enable deterministic regression testing.

## Regression Test

- **Test file**: `config/scripts/__tests__/compute-swe-metrics-cost.test.sh`
- **Test name**: `cost_usd_inferred_from_agent_pricing_when_step_cost_absent`
- **Asserts**: Run `compute-swe-metrics.sh` against a fixture state.yaml that has a step with `total_tokens: 50000` and no `cost_usd` field for agent `developer`. Assert that the output metrics YAML contains `cost_usd > 0` for `developer` and that the value matches `50000 × 3.0 / 1000000 = 0.15` (developer/sonnet input rate).
- **Must fail before fix**: yes — current code produces `cost_usd: 0.000000`
- **Must pass after fix**: yes

## Risk Assessment

### Could This Break Other Things?

- **compute-swe-metrics.sh with proxy agents**: If a step has a real `cost_usd` from LiteLLM (non-zero), the awk block already accumulates it correctly. The post-awk inference only applies when `cost_usd = 0` — so proxy-cost steps are unaffected.
- **register-repo.sh idempotency**: Using `CREATE TABLE IF NOT EXISTS` and `INSERT OR REPLACE` ensures re-runs don't duplicate data. The seed data is static (derived from pricing.yaml + routes.yaml), so this is safe.
- **agent_pricing table missing at duckdb query time**: If `compute-swe-metrics.sh` runs before `register-repo.sh` has initialized the DB (e.g., first-time setup), the duckdb query for pricing will fail. The post-awk block must fall through gracefully (cost_usd stays 0) rather than halting the script.
- **Unknown agents**: If a step references an agent not in `agent_pricing` (e.g., a future agent), the join returns no rows and cost_usd stays 0. This is acceptable and matches current behavior.
- **Historical data**: Existing 20 rows in `per_agent_metrics` have `cost_usd = 0`. The fix does not automatically correct them — a manual `register-repo.sh --rebuild` pass over historical state.yamls is required after the fix lands.

### Rollback Plan

`git revert <fix commit>` reverts both changes atomically. The `agent_pricing` table in DuckDB continues to exist (DDL is additive) but is no longer joined. Metrics return to the current behavior (cost_usd = 0) — no worse than before the fix.

## Out of Scope

- Widening `step_history.usage`, `per_step_metrics`, and `per_agent_metrics` with per-category token fields (`input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`) — deferred per idea.md. Re-evaluate after this fix restores non-zero cost_usd.
- Using output_per_1m or a blended rate in the cost approximation — deferred until token split is available. Using input_per_1m as a lower bound is the explicit choice for this fix.
- Automated re-ingest trigger for historical state.yamls — the operator must run `register-repo.sh --rebuild` manually.
- Unifying the per-agent PER_AGENT_TOKENS fallback with the per-feature PROXY_COST_USD/MODEL_NET_USD fallback chain (lines 252–257) — tracked as a future cleanup, not part of this fix.

<!-- Format contract: contracts/artifact-formats.md § Fix Plan Format Contract -->
