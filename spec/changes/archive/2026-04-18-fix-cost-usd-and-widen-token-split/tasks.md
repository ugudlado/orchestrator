# Tasks — Fix cost_usd = 0 for all per_agent_metrics rows

- [x] T-1: Add regression test fixture and test asserting cost_usd > 0 when step has total_tokens but no cost_usd
  Verify: `bash config/scripts/__tests__/compute-swe-metrics-cost.test.sh` exits non-zero (test FAILS) before T-3 fix is applied; test name `cost_usd_inferred_from_agent_pricing_when_step_cost_absent` is clearly reported as failing.

- [x] T-2: Add agent_pricing DDL + seed to register-repo.sh so the table survives DB re-creation
  Verify: `rm -f /tmp/test-metrics.duckdb && METRICS_DB=/tmp/test-metrics.duckdb bash config/scripts/register-repo.sh --rebuild 2>/dev/null; duckdb /tmp/test-metrics.duckdb "SELECT COUNT(*) FROM agent_pricing;"` returns >= 1.
  depends: T-1

- [x] T-3: Modify PER_AGENT_TOKENS block in compute-swe-metrics.sh to infer cost_usd from agent_pricing when step cost is absent
  Verify: `bash config/scripts/__tests__/compute-swe-metrics-cost.test.sh` exits 0 (test PASSES); regression test `cost_usd_inferred_from_agent_pricing_when_step_cost_absent` reports success.
  depends: T-1, T-2

- [x] T-4: Re-ingest all historical state.yamls and assert zero failing rows in per_agent_metrics
  Verify: After running `register-repo.sh --rebuild`, `duckdb metrics.duckdb "SELECT COUNT(*) FROM per_agent_metrics WHERE total_tokens > 10000 AND (cost_usd = 0 OR cost_usd IS NULL);"` returns 0.
  depends: T-3

- [x] T-5: Document archive state.yaml patches as explicit scope expansion in idea.md and fix-plan.md
  Rationale: T-4 directly edited per_agent_tokens JSON in 5 archived state.yaml files, which is outside the original Affected Files list. The math is correct but the scope expansion is undocumented. fix-plan.md § Affected Files and idea.md § Fix (or a new § Additional Scope) must list the 5 archive paths with a one-line rationale (JSONL aged out; stored token counts authoritative; cost injected using total_tokens × input_per_1m / 1M, same formula as the script fix).
  Verify: `grep '2026-04-17-cross-repo-metrics-duckdb' spec/changes/backlog/fix-cost-usd-and-widen-token-split/fix-plan.md spec/changes/backlog/fix-cost-usd-and-widen-token-split/idea.md` returns matches in both files.
  depends: T-4

- [x] T-6: Add multi-agent test case to compute-swe-metrics-cost.test.sh
  Rationale: The T-1 fixture (state.native-agent.yaml) contains only a single developer step. Commit bb38263 fixed an awk loop break bug (exit-on-unknown-agent) that the single-agent fixture never exercises. Add a second fixture or extend the existing fixture with one step for "unknown-agent" (not in agent_pricing). Add two assertions: developer cost_usd = 0.150000 AND unknown-agent cost_usd = 0.000000 (loop advances past unknown, does not short-circuit the known agent).
  Verify: `bash config/scripts/__tests__/compute-swe-metrics-cost.test.sh` exits 0 with "Results: N passed, 0 failed" where N >= 2 (multi-agent assertions included).
  depends: T-3
