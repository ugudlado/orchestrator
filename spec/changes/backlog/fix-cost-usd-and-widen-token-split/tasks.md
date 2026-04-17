# Tasks — Fix cost_usd = 0 for all per_agent_metrics rows

- [ ] T-1: Add regression test fixture and test asserting cost_usd > 0 when step has total_tokens but no cost_usd
  Verify: `bash config/scripts/__tests__/compute-swe-metrics-cost.test.sh` exits non-zero (test FAILS) before T-2 fix is applied; test name `cost_usd_inferred_from_agent_pricing_when_step_cost_absent` is clearly reported as failing.

- [ ] T-2: Add agent_pricing DDL + seed to register-repo.sh so the table survives DB re-creation
  Verify: `rm -f /tmp/test-metrics.duckdb && METRICS_DB=/tmp/test-metrics.duckdb bash config/scripts/register-repo.sh --rebuild 2>/dev/null; duckdb /tmp/test-metrics.duckdb "SELECT COUNT(*) FROM agent_pricing;"` returns >= 1.
  depends: T-1

- [ ] T-3: Modify PER_AGENT_TOKENS block in compute-swe-metrics.sh to infer cost_usd from agent_pricing when step cost is absent
  Verify: `bash config/scripts/__tests__/compute-swe-metrics-cost.test.sh` exits 0 (test PASSES); regression test `cost_usd_inferred_from_agent_pricing_when_step_cost_absent` reports success.
  depends: T-1, T-2

- [ ] T-4: Re-ingest all historical state.yamls and assert zero failing rows in per_agent_metrics
  Verify: After running `register-repo.sh --rebuild`, `duckdb metrics.duckdb "SELECT COUNT(*) FROM per_agent_metrics WHERE total_tokens > 10000 AND (cost_usd = 0 OR cost_usd IS NULL);"` returns 0.
  depends: T-3
