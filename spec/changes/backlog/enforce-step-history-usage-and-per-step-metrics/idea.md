# Enforce step_history usage capture + add per-step metrics aggregation

## Problem

`compute-swe-metrics.sh` emits a `per_agent_tokens` map and attempts `per_agent_tools`, but:

1. **Most step_history entries are missing the `agent:` field or the full `usage:` block**, so per-agent aggregation undercounts by 5-10x.
2. **There is no per-step-type aggregation at all.** The data exists (step_id + usage are in step_history) but the script never groups by step_id.

**Observed in HL-282** (autopilot-2026-04-17-001):

```yaml
per_agent_tokens: '{"architect":{"total_tokens":77031,"cost_usd":0.000000,"tool_uses":9,"duration_ms":232346,"steps":2},"discoverer":{"total_tokens":47056,"cost_usd":0.000000,"tool_uses":37,"duration_ms":1126753,"steps":1}}'
per_agent_tools: '{}'
```

Only 2 agents shown. Actual spawns this session: 20+ (9 developer, 5+ reviewer, 1 ideator, 1 workflow-improver, plus architect and discoverer). Missing because:
- Developer task entries had `agent: developer` but no full `usage:` block with `total_tokens`
- Reviewer entries were written inline without capturing the agent tool footer
- `tools:` sub-maps were almost never written → `per_agent_tools` is empty

## Two causes

**Cause A — orchestrator laxness in writing state.yaml**

The orchestrate skill says "extract usage data from the agent result and add a usage: block" but there is no validation. When the dispatch loop is driven by a human-in-the-loop (me in this session), it's easy to forget or skip the full block. The aggregation script then silently ignores these entries.

**Cause B — missing per-step aggregation**

`compute-swe-metrics.sh` has two awk passes over step_history (one for per_agent_tokens, one for per_agent_tools) but no third pass that groups by `step_id`. Per-step data is useful for /telemetry ("which steps cost the most") and /learn ("which steps have outsized duration/token cost").

## Scope

**In-scope:**

Part A — Enforce usage capture:
- Add a validator step (or inline check in `archive-completed-change`) that flags step_history entries missing required fields
- Define required fields per step type (all agent-spawning steps must have `agent:` + `usage: { total_tokens }`)
- Non-blocking by default (warning to stderr); optional strict mode via a flag

Part B — Per-step aggregation:
- Add a third awk pass in `compute-swe-metrics.sh` that groups by `step_id`
- Emit `per_step: {explore: {total_tokens, cost_usd, duration_ms, count}, execute-next-task: {...}, ...}`
- Include in the metrics YAML output alongside `per_agent_tokens`
- Ingest into DuckDB schema (either add columns or extract in queries — design decision)

**Out-of-scope:**
- Changing how the orchestrate skill dispatches agents
- Touching the agent tool itself
- DuckDB schema migration for old archived features (they stay as-is; new features get richer data)

## Acceptance criteria

- AC-1: State-validator warning fires when a step_history entry for an agent-spawning step is missing `agent:` or `usage.total_tokens`
- AC-2: `per_step:` block present in `metrics:` output, one entry per distinct `step_id` that executed
- AC-3: Sum of `per_step[*].total_tokens` roughly equals `tokens.total` (off by state-only fallback for the steps themselves — document the math)
- AC-4: /telemetry can query "which steps consumed the most tokens last 5 features"
- AC-5: Per-agent coverage on a fresh autopilot run includes all spawned agents (not just 2-3)

## Priority

Medium — the existing per_agent block is already useful for directionally flagging cost outliers. Full per-step is a nice-to-have until we have more data volume. Worth bundling with the /learn + /telemetry DuckDB migration (HL-282) since those consumers will want to use it.
