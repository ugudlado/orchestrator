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
  - name: fleet
    description: >
      Pass `--fleet` to show metrics across all repos (cross-repo view).
      Default (no flag) shows only the current repo.
    required: false
---

## Invocation

Default: per-repo view of the current repository.
Use `/telemetry --fleet` for a cross-repo view aggregated across all registered repos.

## Variables

```
REPO_ROOT=${REPO_ROOT:-$(git rev-parse --show-toplevel)}
REPO_NAME=${REPO_NAME:-$(basename "$REPO_ROOT")}
ORCHESTRATOR_HOME=${ORCHESTRATOR_HOME:-$HOME/.config/orchestrator}
WORKFLOW_STATE_DIR=${WORKFLOW_STATE_DIR:-$REPO_ROOT/spec/changes}
WORKTREE_ARTIFACT_DIR="${WORKTREE_ARTIFACT_DIR:-${WORKTREE_ROOT:-$REPO_ROOT}/spec/changes}"
METRICS_QUERY=${METRICS_QUERY:-$(git rev-parse --show-toplevel)/config/scripts/metrics-query.sh}
FLEET_FLAG=${FLEET_FLAG:-}   # set to "--fleet" when invoked with --fleet
```

## Execution

### 1. Gather data

**Primary source — DuckDB (archived metrics):**

Invoke `metrics-query.sh` based on scope. All invocations default to per-repo;
pass `$FLEET_FLAG` (which equals `--fleet` when the user invoked `/telemetry --fleet`)
to aggregate across all registered repos.

- **`recent` mode (default):** `$METRICS_QUERY recent-features --limit 5 $FLEET_FLAG`
- **`all` mode:** `$METRICS_QUERY recent-features $FLEET_FLAG`
- **trend analysis:** `$METRICS_QUERY cost-trend $FLEET_FLAG` and `$METRICS_QUERY quality-trend $FLEET_FLAG`

If `metrics-query.sh` exits non-zero or produces no output (DB absent, repo unregistered,
no rows), fall back to reading archived state.yaml files directly:
- **`recent` mode fallback:** `ls -t spec/changes/archive/*/state.yaml | head -5`
- **`all` mode fallback:** `ls -t spec/changes/archive/*/state.yaml`

For each state.yaml returned, extract `feature_id`, `status`, `completed_at`, and the `metrics:` block directly from the YAML.

**Secondary source — active feature state (ephemeral, not in DB):**

Merge `$WORKFLOW_STATE_DIR/*/state.yaml` for currently-in-progress features.
This YAML is ephemeral workflow state, not archived metrics; always read it directly.

### 2. Compute dashboard metrics

From the `recent-features` rows (each row contains `change_id`, `status`,
`completed_at`, and `payload_json` with the full metrics block):

**Cost & Tokens**
- Total cost (USD) — sum of `cost_usd` from `payload_json`
- Average cost per feature
- Total tokens — sum of `total_tokens` (with `input_tokens`/`output_tokens`/`cache_read_input_tokens` breakdown)
- Average cache hit rate — `cache_hit_rate`

**Time**
- Total wall clock minutes — sum of `wall_clock_minutes`
- Average time per feature

**Quality**
- Average review score — `review_score_avg`
- Pass@1 rate — fraction of features where `pass_at_1` > 0
- Average rework rate — `rework_rate`
- Regression rate — `regression_rate`

**Efficiency**
- Average turns per feature — `turns`
- Average tool calls per feature — `tool_calls_count`
- Total retries — sum of `retries_total`
- Retry hotspots — steps with highest retry counts across features

**Code Churn**
- Average files changed per feature — `files_changed`
- Average insertions/deletions per feature — `insertions` / `deletions`

### 3. Per-step timing breakdown (when available)

From `per_step_metrics` view (backed by `step_events`):
- Run `$METRICS_QUERY step-cost-hotspots $FLEET_FLAG --limit 10` for top expensive steps
- Group by phase (from `step_events.phase`), show phase total and per-step cost/duration
- Flag outliers: steps with duration >2x the average for that step type

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

If there are 4+ features in scope, fetch trend data and show trend arrows.
Use `$METRICS_QUERY cost-trend $FLEET_FLAG` for cost trend and
`$METRICS_QUERY quality-trend $FLEET_FLAG` for quality trend.
If either call exits non-zero or returns empty output, skip that trend silently.

- Cost trend: ↑ increasing / ↓ decreasing / → stable
- Quality trend: review scores improving/declining
- Retry trend: retries per feature increasing/decreasing

Compare the first half vs second half of features in the dataset.

### Key rules

- If no archived state.yaml files exist, report "No telemetry data yet. Complete a feature workflow to generate metrics."
- Use `null` values gracefully — skip metrics where the data field is null rather than showing "null"
- Round costs to 2 decimal places, percentages to integers, times to nearest minute
- For per-step timing: only show if at least one state.yaml has `started_at`/`completed_at` in step_history
