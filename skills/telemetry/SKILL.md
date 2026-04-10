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
METRICS_FILE=${METRICS_FILE:-$HOME/.claude/logs/feature-metrics.jsonl}
```

## Execution

### 1. Gather data

Read from these sources (skip any that don't exist):

- **`$METRICS_FILE`** — one JSON line per completed feature (primary source)
- **`$WORKFLOW_STATE_DIR/*/state.yaml`** — active/recent workflows with step_history
- **`spec/changes/archive/*/state.yaml`** — archived workflows with full metrics

Filter by scope:
- `recent` (default): last 5 entries from feature-metrics.jsonl
- `all`: all entries
- `<feature-id>`: single feature match

### 2. Compute dashboard metrics

From feature-metrics.jsonl entries:

**Cost & Tokens**
- Total cost (USD) — sum of `swe_comparable.cost_usd`
- Average cost per feature
- Total tokens — sum of `efficiency.tokens_input` + `efficiency.tokens_output`
- Average cache hit rate — mean of `efficiency.cache_hit_rate`

**Time**
- Total wall clock minutes — sum of `swe_comparable.wall_clock_minutes`
- Average time per feature

**Quality**
- Average review score — mean of `workflow_quality.review_score_avg`
- Pass@1 rate — fraction of features where `workflow_quality.pass_at_1` is true
- Average rework rate — mean of `workflow_quality.rework_rate`
- Regression rate — mean of `workflow_quality.regression_rate`

**Efficiency**
- Average turns per feature — mean of `efficiency.turns`
- Average tool calls per feature — mean of `efficiency.tool_calls`
- Total retries — sum of `retries.total_retries`
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

- If `$METRICS_FILE` doesn't exist or is empty, report "No telemetry data yet. Complete a feature workflow to generate metrics."
- Use `null` values gracefully — skip metrics where the data field is null rather than showing "null"
- Round costs to 2 decimal places, percentages to integers, times to nearest minute
- For per-step timing: only show if at least one state.yaml has `started_at`/`completed_at` in step_history
