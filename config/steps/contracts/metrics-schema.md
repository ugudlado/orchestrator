# Metrics Schema Contract

Canonical definition of the `metrics:` block written by `compute-swe-metrics.sh`
into `spec/changes/archive/<slug>/state.yaml`. All workflow schemas that run
`compute-swe-metrics` produce this block. Consumers (telemetry, learn,
workflow-improver) read it from archived state files.

## Field Registry

All fields under `metrics:` — their type, description, and source:

| Field | Type | Description | Source |
|-------|------|-------------|--------|
| `tokens.input` | integer | Input tokens billed | session JSONL or step_history |
| `tokens.output` | integer | Output tokens billed | session JSONL or step_history |
| `tokens.cache_creation` | integer | Cache-creation tokens | session JSONL or step_history |
| `tokens.cache_read` | integer | Cache-read tokens | session JSONL or step_history |
| `tokens.total` | integer | Total tokens (input + output + cache_creation) | computed |
| `cost.gross_usd` | decimal | Gross cost — all tokens at full price, no cache discount | computed |
| `cost.net_usd` | decimal | Net cost — with cache discounts applied | computed |
| `cost.model` | string | Dominant model (by input token share) | session JSONL |
| `cost.pricing.input` | decimal | Input price ($/1M tokens) | model lookup |
| `cost.pricing.output` | decimal | Output price ($/1M tokens) | model lookup |
| `cost.pricing.cache_read` | decimal | Cache-read price ($/1M tokens) | model lookup |
| `turns` | integer | Number of assistant turns | session JSONL |
| `tool_calls` | integer | Total tool invocations | step_history |
| `api_calls` | integer | Same as turns (API round-trips) | session JSONL |
| `wall_clock_minutes` | decimal | Elapsed wall-clock time | state.yaml timestamps |
| `resolution.tasks_total` | integer | Total tasks in tasks.md | tasks.md |
| `resolution.tasks_planned` | integer | Planned task count (same as total) | tasks.md |
| `resolution.tasks_added` | integer | Tasks added after initial plan | tasks.md |
| `resolution.tasks_completed` | integer | Tasks marked `[x]` | tasks.md |
| `resolution.tasks_failed` | integer | tasks_total − tasks_completed | computed |
| `resolution.resolve_rate` | decimal | tasks_completed / tasks_total | computed |
| `resolution.pass_at_1` | decimal | Fraction passing on first attempt | state.yaml retries |
| `resolution.pass_at_2` | decimal | Fraction passing within 2 attempts | state.yaml retries |
| `resolution.regressions` | integer | Tasks that regressed | state.yaml |
| `resolution.regression_rate` | decimal | regressions / tasks_total | computed |
| `resolution.iterations_completed` | integer | Autopilot iterations with status=completed | autopilot session |
| `resolution.iterations_failed` | integer | Autopilot iterations with status=failed | autopilot session |
| `resolution.iterations_empty` | integer | Autopilot iterations with status=empty_backlog | autopilot session |
| `retries.total` | integer | Sum of all task retry counts | state.yaml |
| `human_interventions` | integer | Manual interventions (always 0 for fully automated runs) | — |
| `rework_commits` | integer | Commits with `fix:` prefix | git log |
| `rework_rate` | decimal | rework_commits / total_commits | computed |
| `churn.files_changed` | integer | Unique files changed across feature commits | git diff |
| `churn.insertions` | integer | Lines inserted | git diff --stat |
| `churn.deletions` | integer | Lines deleted | git diff --stat |
| `churn.total_commits` | integer | Total commits for this change | git log |
| `review_scores` | array | Overall scores from passing run-phase-review steps (verdict `pass`; legacy entries without verdict included) | step_history |
| `review_score_avg` | decimal | Mean of review_scores — excludes `needs_work` and `incomplete_phase` attempts | computed |
| `lint_delta` | integer | Lint finding delta (always 0; future use) | — |
| `category` | string | Schema name (feature, bugfix, autopilot) | state.yaml schema field |
| `benchmarks.cost_per_task_usd` | decimal | net_usd / tasks_total | computed |
| `benchmarks.cost_per_resolution_usd` | decimal | net_usd / tasks_completed | computed |
| `benchmarks.tokens_per_task` | integer | tokens.total / tasks_total | computed |
| `benchmarks.tokens_per_resolution` | integer | tokens.total / tasks_completed | computed |
| `benchmarks.input_output_ratio` | decimal | (input + cache_creation) / output | computed |
| `benchmarks.cache_hit_rate` | decimal | cache_read / (input + cache_creation + cache_read) | computed |
| `per_agent_tokens` | JSON string | Per-agent token/tool/duration totals | step_history |
| `per_agent_tools` | JSON string | Per-agent tool breakdown | step_history |
| `per_step.<step_id>.total_tokens` | integer | Total tokens for this step_id (all executions summed) | step_history |
| `per_step.<step_id>.tool_uses` | integer | Total tool invocations for this step_id (all executions summed) | step_history |
| `per_step.<step_id>.duration_ms` | integer | Total duration in ms for this step_id (all executions summed) | step_history |
| `per_step.<step_id>.executions` | integer | Execution count for this step_id — retry-inclusive. A count of 2 means the step ran twice (one retry). | step_history |
| `estimate_vs_actual.tokens_predicted` | integer | Token count projected by preview-route before the run | route_preview.estimate.tokens |
| `estimate_vs_actual.tokens_actual` | integer | Token count billed after the run | tokens.total |
| `estimate_vs_actual.tokens_delta_pct` | decimal | (actual − predicted) / predicted. Negative = overestimate. | computed |
| `estimate_vs_actual.cost_predicted_usd` | decimal | Cost projected by preview-route before the run | route_preview.estimate.cost_usd |
| `estimate_vs_actual.cost_actual_usd` | decimal | Net cost after the run | cost.net_usd |
| `estimate_vs_actual.cost_delta_pct` | decimal | (actual − predicted) / predicted. Negative = overestimate. | computed |

## Per-Schema Variants

Which fields are **required** (R), **null** (~, explicit YAML null), or **omitted** (—)
for each workflow schema:

| Field | feature | bugfix | autopilot |
|-------|---------|--------|-----------|
| `tokens.*` | R | R | R |
| `cost.*` | R | R | R |
| `turns` | R | R | R |
| `tool_calls` | R | R | R |
| `wall_clock_minutes` | R | R | R |
| `resolution.tasks_total` | R | R | ~ |
| `resolution.resolve_rate` | R | R | ~ |
| `resolution.pass_at_1` | R | R | ~ |
| `resolution.pass_at_2` | R | R | ~ |
| `resolution.regression_rate` | R | R | ~ |
| `resolution.iterations_completed` | — | — | R |
| `resolution.iterations_failed` | — | — | R |
| `resolution.iterations_empty` | — | — | R |
| `churn.*` | R | R | R |
| `review_scores` | R | R | — |
| `review_score_avg` | R | R | — |
| `category` | R | R | R |
| `benchmarks.*` | R | R | R |
| `per_agent_tokens` | R | R | R |
| `per_agent_tools` | R | R | R |
| `per_step.*` | R | R | R |
| `estimate_vs_actual.*` | O | O | O |

When `feature` runs with `--light`, all required fields remain required —
review scores and task counts are still emitted, just against a lower
threshold (7 vs 9). `metrics.category` stays `feature` regardless of the
light flag; consumers that need to distinguish can inspect `state.yaml`'s
`flags.light` field.

**R** = required, present with a real value
**~** = explicit YAML null (key is present, value is `~`)
**—** = key is omitted entirely from the block
**O** = optional; key is present only when the upstream data exists (see `estimate_vs_actual` below)

## Consumer Contract

### Null Contract

When a field is marked **~** (explicit YAML null) in the table above:

- The key IS present in the YAML block.
- The value is the YAML null literal `~` (not the string `"null"`, not 0, not absent).
- Consumers MUST use null-skip rather than null-render. If a consumer displays
  metric fields, it must omit fields where the value is null — not display "null"
  to the user.
- This is already documented in `skills/telemetry/SKILL.md:143`:
  "Use null values gracefully — skip metrics where the data field is null."

Example autopilot resolution block:

```yaml
resolution:
  resolve_rate: ~
  pass_at_1: ~
  pass_at_2: ~
  regression_rate: ~
  tasks_total: ~
```

### Omit Contract

When a field is marked **—** (omitted) in the table above:

- The key is NOT present in the YAML block.
- Consumers that iterate metrics fields MUST NOT assume `review_scores` is
  present. Check for key existence before accessing it.
- This applies to: `review_scores`, `review_score_avg` for autopilot.

Example: autopilot output does not contain a `review_scores:` line at all.

### Category Field

The `metrics.category` field contains the schema name from `state.yaml`. Consumers
that aggregate across schemas MUST group by `metrics.category` before computing
cross-schema statistics. Resolution fields are only meaningful for
`category: feature|bugfix`.

### Stable Block Shape

The `resolution:` key is always present for all schemas (including autopilot),
with null values for inapplicable fields. This keeps the block shape
stable so consumers can navigate to `metrics.resolution` without a key-existence
guard, then null-check individual fields as needed.

### Autopilot Iteration Counts

For `category: autopilot`, the `resolution.*` fields carry iteration counts instead
of task resolution metrics:

- `resolution.iterations_completed` — count of iterations with `status: completed`
- `resolution.iterations_failed` — count of iterations with `status: failed`
- `resolution.iterations_empty` — count of iterations with `status: empty_backlog`
- Other `resolution.*` fields remain null (`~`)

### Estimate vs Actual

`estimate_vs_actual` is emitted only when the `preview-route` step ran during
the specify/diagnose phase and produced a non-null estimate (i.e., archive
history existed at that time). When absent, the entire block — including the
`estimate_vs_actual:` key — is omitted, not null. Consumers MUST check for key
existence before accessing any field under it.

The `*_delta_pct` fields are signed decimals in the range (−1.0, +∞):

- `0.0`  → prediction matched actual exactly
- `+0.25` → actual was 25% over prediction (underestimate)
- `-0.25` → actual was 25% under prediction (overestimate)
- `-1.0` → actual was zero (e.g., native agents with no proxy cost recorded)

Use these deltas as the learning signal for future estimates. No action is
taken automatically — telemetry surfaces the trend so humans (or a future
workflow-improver rule) can tune `config/pricing.yaml` or the estimator.

## Per-Step Aggregation

The `per_step` block is written by `compute-swe-metrics.sh` from `step_history` entries.
One entry per distinct `step_id` is emitted. All token and duration fields are summed
across all executions of the step (retry-inclusive). Key semantics:

- **`executions`** is retry-inclusive: a step that ran once has `executions: 1`; a step
  that was retried once has `executions: 2`. This gives the cost signal for retry-prone steps.
- **Inline steps** (`agent: inline`) appear in `per_step` with `total_tokens: 0` but non-zero
  `tool_uses` and `duration_ms`. This is consistent with how inline steps appear in
  `per_agent_tokens` (zero tokens, non-zero duration in the `inline` agent bucket).
- **Token sum invariant**: when `metrics.tokens.total` is derived from `step_history`
  (JSONL enrichment absent or failed), `sum(per_step[*].total_tokens) == metrics.tokens.total`.
  When JSONL enrichment succeeds, `metrics.tokens.total` may include orchestrator tokens
  not captured per-step — the per_step sum covers only sub-agent step tokens.
- **Backward compatibility**: archived `state.yaml` files that predate this block do not
  have `per_step`. Consumers MUST check for key existence before accessing any field under it.

## step_history `usage:` Sub-Block (short-name schema)

Added by the `subprocess-per-step-observability` feature. Step adapters and inline
agents write usage data under a `usage:` key in the `step_history` entry using short,
human-readable field names. The `orchestrator next` dispatcher maps these short names
to OpenTelemetry GenAI column names at upsert time — consuming code never uses the OTel
names directly in state.yaml.

### Short-Name Field Registry

| state.yaml field (short) | Type | Description | Maps to `step_events` column |
|--------------------------|------|-------------|-------------------------------|
| `usage.input_tokens` | integer | Input tokens billed for this step invocation | `gen_ai_usage_input_tokens` |
| `usage.output_tokens` | integer | Output tokens billed | `gen_ai_usage_output_tokens` |
| `usage.cache_read_input_tokens` | integer | Cache-read tokens (discounted tier) | `gen_ai_usage_cache_read_input_tokens` |
| `usage.cost_usd` | decimal | Attributable cost in USD for this step invocation | `gen_ai_usage_cost_usd` |
| `usage.model` | string | Model identifier (e.g., `claude-sonnet-4-5`) | `gen_ai_request_model` |
| `usage.duration_ms` | integer | Wall-clock duration of the step in milliseconds | `duration_ms` |
| `usage.tool_calls` | map (string→integer) | Per-tool invocation counts (e.g., `{Read: 32, Grep: 8}`) | `tool_calls_json` (JSON-serialised) |

All fields under `usage:` are optional. Inline steps (contract has no `run:` field)
may omit `usage:` entirely or provide an empty block; the resulting `step_events` row
carries NULL in all OTel token/cost columns, which is correct — inline steps produce
no attributable token usage at the step level.

### Example step_history entry with usage

```yaml
step_history:
  - step_id: explore
    phase: specify
    status: completed
    agent: discoverer
    attempt: 1
    started_at: "2026-04-17T21:12:42Z"
    ended_at: "2026-04-17T21:27:42Z"
    artifacts:
      - discovery.md
    usage:
      input_tokens: 120000
      output_tokens: 18000
      cache_read_input_tokens: 85000
      cost_usd: 2.47
      tool_calls:
        Read: 32
        Grep: 8
        Bash: 4
      duration_ms: 912000
      model: "claude-sonnet-4-5"
```

---

## `step_events` DuckDB Table

The `orchestrator next` CLI maintains a `step_events` table in `metrics.duckdb`
alongside the existing `features` table. Every terminal `step_history` entry is
upserted into this table on each `orchestrator next` call (idempotent — re-running
produces identical rows).

### Purpose

`step_events` provides step-granularity observability for cross-feature queries
(e.g., token cost per step, retry frequency by step_id, agent-level rollup).
The `features` table produced by `compute-swe-metrics.sh` remains the canonical
feature-level aggregate; `step_events` is the per-step query plane.

### Schema

```sql
CREATE TABLE IF NOT EXISTS step_events (
  -- Dimension keys (all non-null) — composite primary key
  repo_root   VARCHAR NOT NULL,
  change_id   VARCHAR NOT NULL,
  phase       VARCHAR NOT NULL,
  step_id     VARCHAR NOT NULL,
  attempt     INTEGER NOT NULL,

  -- Descriptors
  agent_name  VARCHAR NOT NULL,    -- 'discoverer', 'reviewer', 'inline', etc.
  status      VARCHAR NOT NULL,    -- completed|failed|blocked|escalate_to_architect

  -- Timestamps
  started_at  TIMESTAMP,
  ended_at    TIMESTAMP,
  duration_ms BIGINT,

  -- OpenTelemetry GenAI semantic convention columns
  gen_ai_request_model                   VARCHAR,
  gen_ai_usage_input_tokens              BIGINT,
  gen_ai_usage_output_tokens             BIGINT,
  gen_ai_usage_cache_read_input_tokens   BIGINT,
  gen_ai_usage_cost_usd                  DOUBLE,

  -- Structured payloads (JSON strings for flexible queries)
  tool_calls_json  VARCHAR,        -- e.g., {"Read": 32, "Grep": 8}
  artifacts_json   VARCHAR,        -- e.g., ["discovery.md"]
  escalation_json  VARCHAR,        -- non-null only for escalate_to_architect rows

  upserted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

  PRIMARY KEY (repo_root, change_id, phase, step_id, attempt, status)
);
```

### Why `status` is in the Primary Key

A single `(phase, step_id, attempt)` may legitimately produce more than one terminal
`step_history` entry when an architect escalation occurs. The escalated attempt is
not charged a retry — the developer re-spawns with the architect decision and the same
`attempt` number. This produces an `escalate_to_architect` entry followed by a
`completed` entry at the same `(phase, step_id, attempt)`. Including `status` in the
primary key preserves the full escalation audit trail in `step_events`.

Rollup queries that want only terminal outcomes should filter
`status IN ('completed', 'failed', 'blocked')` (excluding `escalate_to_architect`).

### Upsert Pattern

The table is populated via `INSERT OR REPLACE` using parameterised
`duckdb.execute(sql, params)` — no string interpolation. The `change_id` is
validated against `^[a-z0-9][a-z0-9-]*$` (slug guard) before any INSERT, per the
`metrics-db-derived` learning.

### Example Rollup Query

```sql
-- Phase-level token and cost totals for a change
SELECT
  phase,
  SUM(gen_ai_usage_input_tokens)  AS input_tokens,
  SUM(gen_ai_usage_output_tokens) AS output_tokens,
  SUM(gen_ai_usage_cost_usd)      AS cost_usd
FROM step_events
WHERE change_id = 'my-feature'
  AND status IN ('completed', 'failed')
GROUP BY phase
ORDER BY phase;
```

---

## Future Schemas

When adding a new workflow schema, choose the appropriate contract:

- If the schema executes discrete tasks with pass/fail outcomes → use the
  feature/bugfix path (full resolution block, real values).
- If the schema is exploratory or composite with no discrete task outcomes →
  use the autopilot path (null resolution, omit review_scores).

Document the choice in this file's Per-Schema Variants table.
