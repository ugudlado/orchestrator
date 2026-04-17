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
| `review_scores` | array | Overall scores from run-phase-review steps | step_history |
| `review_score_avg` | decimal | Mean of review_scores | computed |
| `lint_delta` | integer | Lint finding delta (always 0; future use) | — |
| `category` | string | Schema name (feature, bugfix, spike, autopilot) | state.yaml schema field |
| `benchmarks.cost_per_task_usd` | decimal | net_usd / tasks_total | computed |
| `benchmarks.cost_per_resolution_usd` | decimal | net_usd / tasks_completed | computed |
| `benchmarks.tokens_per_task` | integer | tokens.total / tasks_total | computed |
| `benchmarks.tokens_per_resolution` | integer | tokens.total / tasks_completed | computed |
| `benchmarks.input_output_ratio` | decimal | (input + cache_creation) / output | computed |
| `benchmarks.cache_hit_rate` | decimal | cache_read / (input + cache_creation + cache_read) | computed |
| `per_agent_tokens` | JSON string | Per-agent token/tool/duration totals | step_history |
| `per_agent_tools` | JSON string | Per-agent tool breakdown | step_history |
| `estimate_vs_actual.tokens_predicted` | integer | Token count projected by preview-route before the run | route_preview.estimate.tokens |
| `estimate_vs_actual.tokens_actual` | integer | Token count billed after the run | tokens.total |
| `estimate_vs_actual.tokens_delta_pct` | decimal | (actual − predicted) / predicted. Negative = overestimate. | computed |
| `estimate_vs_actual.cost_predicted_usd` | decimal | Cost projected by preview-route before the run | route_preview.estimate.cost_usd |
| `estimate_vs_actual.cost_actual_usd` | decimal | Net cost after the run | cost.net_usd |
| `estimate_vs_actual.cost_delta_pct` | decimal | (actual − predicted) / predicted. Negative = overestimate. | computed |

## Per-Schema Variants

Which fields are **required** (R), **null** (~, explicit YAML null), or **omitted** (—)
for each workflow schema:

| Field | feature | bugfix | spike | autopilot |
|-------|---------|--------|-------|-----------|
| `tokens.*` | R | R | R | R |
| `cost.*` | R | R | R | R |
| `turns` | R | R | R | R |
| `tool_calls` | R | R | R | R |
| `wall_clock_minutes` | R | R | R | R |
| `resolution.tasks_total` | R | R | ~ | ~ |
| `resolution.resolve_rate` | R | R | ~ | ~ |
| `resolution.pass_at_1` | R | R | ~ | ~ |
| `resolution.pass_at_2` | R | R | ~ | ~ |
| `resolution.regression_rate` | R | R | ~ | ~ |
| `resolution.iterations_completed` | — | — | — | R |
| `resolution.iterations_failed` | — | — | — | R |
| `resolution.iterations_empty` | — | — | — | R |
| `churn.*` | R | R | R | R |
| `review_scores` | R | R | — | — |
| `review_score_avg` | R | R | — | — |
| `category` | R | R | R | R |
| `benchmarks.*` | R | R | R | R |
| `per_agent_tokens` | R | R | R | R |
| `per_agent_tools` | R | R | R | R |
| `estimate_vs_actual.*` | O | O | O | O |

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

Example spike resolution block:

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
- This applies to: `review_scores`, `review_score_avg` for spike and autopilot.

Example: spike output does not contain a `review_scores:` line at all.

### Category Field

The `metrics.category` field contains the schema name from `state.yaml`. Consumers
that aggregate across schemas MUST group by `metrics.category` before computing
cross-schema statistics. Resolution fields are only meaningful for
`category: feature|bugfix`.

### Stable Block Shape

The `resolution:` key is always present for all schemas (including spike and
autopilot), with null values for inapplicable fields. This keeps the block shape
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

## Future Schemas

When adding a new workflow schema, choose the appropriate contract:

- If the schema executes discrete tasks with pass/fail outcomes → use the
  feature/bugfix path (full resolution block, real values).
- If the schema is exploratory or composite with no discrete task outcomes →
  use the spike/autopilot path (null resolution, omit review_scores).

Document the choice in this file's Per-Schema Variants table.
