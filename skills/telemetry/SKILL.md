---
name: telemetry
description: "Show workflow metrics dashboard. Use when user says \"telemetry\", \"show metrics\", \"workflow health\", \"dashboard\"."
user-invocable: true
args:
  - name: scope
    description: >
      What to show: "recent" (last 5 features, default), "all" (all features),
      or a feature ID for a single feature's breakdown.
    required: false
---

## Variables

```
REPO_ROOT=${REPO_ROOT:-$(git rev-parse --show-toplevel)}
REPO_NAME=${REPO_NAME:-$(basename "$REPO_ROOT")}
ORCHESTRATOR_HOME=${ORCHESTRATOR_HOME:-$HOME/.config/orchestrator}
WORKFLOW_STATE_DIR=${WORKFLOW_STATE_DIR:-$REPO_ROOT/.state}
```

## Execution

### 1. Gather data

Read from these sources (skip any that don't exist):

- **`spec/changes/archive/*/state.yaml`** — archived workflows with full metrics (primary source)
- **`$WORKFLOW_STATE_DIR/*/state.yaml`** — active/recent workflows with step_history

Each archived state.yaml contains a `metrics:` block with tokens, cost, resolution,
churn, review_scores, per_agent_tokens, and per_agent_tools data.

Filter by scope:
- `recent` (default): last 5 archived state.yaml files (sorted by modification time)
- `all`: all archived state.yaml files
- `<feature-id>`: single feature match by change_id or slug

### 2. Compute dashboard metrics

From archived state.yaml `metrics:` blocks:

**Cost & Tokens**
- Total cost (USD) — sum of `metrics.cost.net_usd`
- Average cost per feature
- Total tokens — sum of `metrics.tokens.total` (with input/output/cache breakdown)
- Average cache hit rate — `metrics.benchmarks.cache_hit_rate`

**Time**
- Total wall clock minutes — sum of `metrics.wall_clock_minutes`
- Average time per feature

**Quality**
- Average review score — `metrics.review_score_avg`
- Pass@1 rate — fraction of features where `metrics.resolution.pass_at_1` > 0
- Average rework rate — `metrics.rework_rate`
- Regression rate — `metrics.resolution.regression_rate`

**Efficiency**
- Average turns per feature — `metrics.turns`
- Average tool calls per feature — `metrics.tool_calls`
- Total retries — sum of `metrics.retries.total`
- Retry hotspots — steps with highest retry counts across features

**Code Churn**
- Average files changed per feature
- Average insertions/deletions per feature

### 3. Per-step timing breakdown (when available)

From state.yaml step_history entries that have `started_at` and `completed_at`:

- Compute duration per step: `completed_at - started_at`
- Group by phase, show phase total and per-step breakdown
- Flag outliers: steps taking >2x the average for that step type

### 4. Render dashboard

Present as a formatted text dashboard:

```
═══════════════════════════════════════════════════
  WORKFLOW TELEMETRY — <repo_name>
  Scope: <recent|all|feature-id> (<N> features)
═══════════════════════════════════════════════════

  COST & TOKENS
  ─────────────────────────────────────────────────
  Total cost:        $XX.XX USD
  Avg per feature:   $X.XX USD
  Total tokens:      X.XM (in: X.XM, out: X.XM)
  Cache hit rate:    XX%

  TIME
  ─────────────────────────────────────────────────
  Total time:        XXh XXm
  Avg per feature:   XXm
  Fastest:           XXm (<feature-id>)
  Slowest:           XXm (<feature-id>)

  QUALITY
  ─────────────────────────────────────────────────
  Avg review score:  X.X / 10
  Pass@1 rate:       XX%
  Rework rate:       XX%
  Regression rate:   XX%

  EFFICIENCY
  ─────────────────────────────────────────────────
  Avg turns:         XX per feature
  Avg tool calls:    XX per feature
  Total retries:     XX
  Retry hotspots:    <step> (XX), <step> (XX)

  CODE CHURN
  ─────────────────────────────────────────────────
  Avg files changed: XX
  Avg insertions:    XX
  Avg deletions:     XX

  PER-PHASE TIMING (from step_history)
  ─────────────────────────────────────────────────
  specify:     XXm  [explore: Xm, artifacts: Xm, review: Xm]
  implement:   XXm  [tasks: Xm, simplify: Xm, review: Xm]
  complete:    XXm  [verify: Xm, learn: Xm, archive: Xm]

═══════════════════════════════════════════════════
```

### 5. Trend analysis (when >3 features)

If there are 4+ features in scope, show trend arrows:

- Cost trend: ↑ increasing / ↓ decreasing / → stable
- Quality trend: review scores improving/declining
- Retry trend: retries per feature increasing/decreasing

Compare the first half vs second half of features in the dataset.

### Key rules

- If no archived state.yaml files exist, report "No telemetry data yet. Complete a feature workflow to generate metrics."
- Use `null` values gracefully — skip metrics where the data field is null rather than showing "null"
- Round costs to 2 decimal places, percentages to integers, times to nearest minute
- For per-step timing: only show if at least one state.yaml has `started_at`/`completed_at` in step_history
