# Design: Single-Source Metrics via Step Events

## Context

Three parallel aggregators read the same underlying data and disagree. The goal is
one source of truth — DuckDB. Ingestion pulls from every data source (JSONL, tasks.md,
git log, state.yaml) once, at complete-phase time, and lands everything in DuckDB
rows. Readers become thin projections. This design operates within the constraint
that the `state.yaml.metrics` block shape must remain byte-compatible for consumers
(`register-repo.sh`, `skills/telemetry/SKILL.md`, `agents/workflow-improver.md`).

## Goals / Non-Goals

### Goals

- Every metric in `config/steps/contracts/metrics-schema.md` is reachable via one
  SQL query plane (`orchestrator metrics`).
- `compute-swe-metrics.sh` and `read-sub-state-metrics.sh` contain zero parsing,
  zero `git log`, zero JSONL reads.
- Wire contracts (`state.yaml.metrics` keys, `orchestrator cost` JSON shape) are
  strictly preserved (supersets allowed; removals are not).
- Migration is idempotent; no external tool required.

### Non-Goals

- Historical backfill of archived state.yaml files.
- Moving pricing into DuckDB (stays in `config/pricing.yaml`).
- Changing JSONL aggregation semantics.
- Re-designing the `step_history` dispatcher-memory shape.

## Approaches Considered

### Approach A (rejected): Thin wrapper over `orchestrator cost --format json`

The iter-2 architect design. `compute-swe-metrics.sh` shells out to
`orchestrator cost --format json` and projects to YAML. Rejected because
`_totals()` (`cost_report.py:62–95`) does not surface `cache_creation_input_tokens`,
`turns`, `gross_usd`, `cost.model`, or `cost.pricing.*` — five fields required by
`metrics-schema.md`. The architect's hybrid workaround kept JSONL parsing inside the
wrapper, contradicting the whole feature premise. See
`.state/autopilot/archive/aborted/2026-04-20-single-source-metrics-via-step-events/retro.md`
§ISSUE-32.

### Approach C (rejected): Leave existing scripts; add DuckDB as optional supplement

Doesn't close the $0 vs $0.246 gap; leaves 736 lines of shell as canonical.

### Selected Approach: B — Widen DuckDB + new table + new subcommand + thin wrappers

Widen `step_events` by exactly one column (`turns`). Extend `_totals()` to surface
existing stored columns (`cache_creation_input_tokens`, `cache_read_input_tokens`)
plus derived fields (`gross_usd`, `pricing.*`). Add one new table (`feature_metrics`)
for the non-step-granular data (resolution, churn, reviews, retries). Add one new
step (`ingest-feature-metrics`) that populates the new table. Expose via a new
top-level subcommand (`orchestrator metrics`). Rewrite both wrapper scripts as thin
projections. Preserves all wire contracts; eliminates all parallel reads.

## High-Level Design

### Architecture Overview

```
                ┌──────────────────────────────────────────────────┐
                │  orchestrator record (existing — turns passthru) │
                │   - jsonl_usage._aggregate() → usage['turns']    │
                │   - upsert_step_event() writes turns column       │
                └────────────────────┬─────────────────────────────┘
                                     │ per-step
                                     ▼
                         ┌─────────────────────────┐
                         │  step_events (DuckDB)   │    ← + turns BIGINT
                         └───────────┬─────────────┘
                                     │
        ┌────────────────────────────┴─────────────────────────────┐
        │ ingest-feature-metrics (new, complete-phase)             │
        │   reads tasks.md + git log + state.yaml                  │
        │   upserts one row into feature_metrics                   │
        └────────────────────────────┬─────────────────────────────┘
                                     │
                         ┌───────────▼─────────────┐
                         │  feature_metrics (new)  │
                         └───────────┬─────────────┘
                                     │
        ┌────────────────────────────▼─────────────────────────────┐
        │ orchestrator metrics --change-id X --format json         │
        │   JOINs step_events + feature_metrics + feature_complexity │
        │   + pricing.yaml lookup                                    │
        │   → flat JSON keyed to metrics-schema.md                   │
        └────────────────────────────┬─────────────────────────────┘
                                     │
                    ┌────────────────┴────────────────┐
                    ▼                                 ▼
       compute-swe-metrics.sh (~50)       read-sub-state-metrics.sh (~30)
       emits full metrics: block          emits narrow (tokens,dur,churn)
```

### Key Abstractions

- **`step_events`**: per-step observability plane. Owns: tokens, cost, model,
  turns, duration, tool counts, agent, status, timestamps. One row per
  `(repo_root, change_id, phase, step_id, attempt, status)`.
- **`feature_metrics`**: per-feature aggregate plane. Owns: resolution, churn,
  retries, reviews, wall-clock. One row per `(repo_root, change_id)`. Written
  once per feature by `ingest-feature-metrics`.
- **`orchestrator metrics`**: composition layer. Joins the two tables + pricing
  + feature_complexity into one flat dict matching `metrics-schema.md`.
- **Thin wrappers**: pure projection — they know nothing except how to shell out
  and re-shape JSON to YAML.

## Low-Level Design

### Components

#### 1. `step_events.turns` column (FR-1)

**File**: `config/scripts/orchestrator_next/upsert.py`

Changes:
- `_DDL_STEP_EVENTS`: add `turns BIGINT` column (lines ~27–53).
- `_migrate_step_events` (lines ~186–217): add branch — if `turns` not in `existing`,
  run `ALTER TABLE step_events ADD COLUMN turns BIGINT`. Same idempotent pattern
  used for `cache_creation_input_tokens` at lines 213–215.
- `_INSERT_OR_REPLACE` (lines ~113–136): add `turns` column + placeholder.
- `upsert_step_event` (line ~332): pull `usage.get("turns")` and append to `params`.
- `upsert_synthetic_event` (line ~438): same.

`jsonl_usage._aggregate()` at line 101 already computes `turns`; `record.py` line 374
already copies `turns` from `jsonl_usage` into `usage` (verified — the key is in the
for-loop). So the value is already flowing to `usage["turns"]`; upsert just needs to
write it to DuckDB.

#### 2. `_totals()` widening (FR-2)

**File**: `config/scripts/orchestrator_next/cost_report.py`, `_totals()` lines 62–95.

Before (abridged):
```python
sql = """
SELECT
  COALESCE(SUM(cost_usd), 0.0)    AS cost_usd,
  COALESCE(SUM(input_tokens), 0)  AS input_tokens,
  COALESCE(SUM(output_tokens), 0) AS output_tokens,
  COALESCE(SUM(duration_ms), 0)   AS duration_ms,
  COUNT(*)                        AS step_count
FROM step_events
WHERE repo_root = ? AND change_id = ?
"""
```

After:
```python
sql = """
SELECT
  COALESCE(SUM(cost_usd), 0.0)                    AS cost_usd,
  COALESCE(SUM(input_tokens), 0)                  AS input_tokens,
  COALESCE(SUM(output_tokens), 0)                 AS output_tokens,
  COALESCE(SUM(cache_creation_input_tokens), 0)   AS cache_creation_input_tokens,
  COALESCE(SUM(cache_read_input_tokens), 0)       AS cache_read_input_tokens,
  COALESCE(SUM(turns), 0)                         AS turns,
  COALESCE(SUM(duration_ms), 0)                   AS duration_ms,
  COUNT(*)                                        AS step_count
FROM step_events
WHERE repo_root = ? AND change_id = ?
"""
```

Dominant-model resolution (separate query — reuses the existing `_per_model` pattern):
```sql
SELECT model FROM step_events
WHERE repo_root = ? AND change_id = ? AND model IS NOT NULL
GROUP BY model
ORDER BY SUM(input_tokens) DESC NULLS LAST
LIMIT 1
```

`gross_usd` computed in Python on the returned row:
```python
price = _load_pricing_for(model)  # from pricing.yaml, cached
gross = (
    input_tok    * (price.get("input") or 0) / 1_000_000
    + cache_create * (price.get("cache_creation") or price.get("input") or 0) / 1_000_000
    + cache_read * (price.get("cache_read") or 0) / 1_000_000
    + output_tok   * (price.get("output") or 0) / 1_000_000
)
```

(`net_usd` is the existing `cost_usd` sum — already cache-discounted by
`record._compute_cost_usd` at write time.)

Return dict extended with: `cache_creation_input_tokens`, `cache_read_input_tokens`,
`turns`, `gross_usd`, `model`, `pricing` (a nested dict with `input`, `output`,
`cache_read`, `cache_creation`).

The existing `aggregate_feature()` return dict (`{totals, per_phase, per_agent, ...}`)
is a strict superset — callers of `orchestrator cost --format json` see new top-level
keys but no removed ones.

#### 3. `feature_metrics` table (FR-3)

**File**: `config/scripts/orchestrator_next/upsert.py`

New DDL, added alongside `_DDL_FEATURE_COMPLEXITY` (line ~82):
```python
_DDL_FEATURE_METRICS = """
CREATE TABLE IF NOT EXISTS feature_metrics (
  repo_root          VARCHAR NOT NULL,
  change_id          VARCHAR NOT NULL,
  schema_name        VARCHAR,
  -- Resolution
  tasks_total        INTEGER,
  tasks_planned      INTEGER,
  tasks_added        INTEGER,
  tasks_completed    INTEGER,
  tasks_failed       INTEGER,
  resolve_rate       DOUBLE,
  pass_at_1          DOUBLE,
  pass_at_2          DOUBLE,
  regressions        INTEGER,
  regression_rate    DOUBLE,
  -- Retries / interventions
  retries_total      INTEGER,
  human_interventions INTEGER,
  -- Churn
  files_changed      INTEGER,
  insertions         INTEGER,
  deletions          INTEGER,
  total_commits      INTEGER,
  rework_commits     INTEGER,
  rework_rate        DOUBLE,
  -- Reviews
  review_scores_json VARCHAR,         -- JSON array
  review_score_avg   DOUBLE,
  -- Timing
  wall_clock_minutes DOUBLE,
  -- Audit
  source             VARCHAR,          -- e.g. "ingest-feature-metrics@<ts>"
  computed_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (repo_root, change_id)
)
"""

_INSERT_FEATURE_METRICS = """
INSERT OR REPLACE INTO feature_metrics (
  repo_root, change_id, schema_name,
  tasks_total, tasks_planned, tasks_added, tasks_completed, tasks_failed,
  resolve_rate, pass_at_1, pass_at_2, regressions, regression_rate,
  retries_total, human_interventions,
  files_changed, insertions, deletions, total_commits, rework_commits, rework_rate,
  review_scores_json, review_score_avg,
  wall_clock_minutes,
  source
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
```

`ensure_schema()` runs `db.execute(_DDL_FEATURE_METRICS)` after
`_DDL_FEATURE_COMPLEXITY`. DDL is idempotent via `IF NOT EXISTS`; no migration
function needed for the first rollout, but a `_migrate_feature_metrics()` hook is
reserved for future column additions using the same `DESCRIBE` pattern as
`_migrate_step_events`.

New public helper: `upsert_feature_metrics(db, row: dict) -> None` — validates
change_id slug, runs `_INSERT_FEATURE_METRICS` with positional params.

#### 4. `ingest-feature-metrics` step (FR-4)

**Files**:
- `config/steps/ingest-feature-metrics.yaml` (new contract)
- `scripts/inline/ingest-feature-metrics.py` (new, ~150 lines)

Step contract:
```yaml
id: ingest-feature-metrics
inline: true
run: scripts/inline/ingest-feature-metrics.py
version: 1

intent: Populate feature_metrics DuckDB row from tasks.md + git log + state.yaml.

inputs:
  - change_completed_marker   # from mark-change-completed
  - project_storage_config

rules:
  - MUST run after mark-change-completed (reads completed_at).
  - MUST run before compute-swe-metrics (compute-swe-metrics queries feature_metrics).
  - Fail loud on missing tasks.md, git command failure, or DuckDB error.

instruction: |
  Invoke the Python step with:
    $ORCHESTRATOR_HOME/scripts/inline/ingest-feature-metrics.py \
      --state-yaml "$WORKFLOW_STATE_DIR/$CHANGE_ID/state.yaml"
  Exit non-zero on any error. Append the step_history entry via
  `orchestrator record` (MUST use record, MUST NOT edit state.yaml directly).

verify:
  - feature_metrics row exists for (repo_root, change_id) after the step
  - step_history has an ingest-feature-metrics entry with status=completed

outputs:
  - feature_metrics_ingested
```

Python sketch (`ingest-feature-metrics.py`):
```python
# Inputs: --state-yaml <path>
# Outputs to stdout: {"feature_metrics_ingested": true} JSON (for orchestrator record)
# Exit: 0 on success, 1 on any failure (fail loud per OQ-2)

def main():
    state = yaml.safe_load(open(args.state_yaml))
    repo_root  = state["repo_root"]
    change_id  = state["change_id"]
    schema     = state.get("schema", "feature")
    worktree   = state.get("worktree_path", repo_root)

    # Resolution (tasks.md) — fail loud if missing for feature/bugfix
    tasks_md = Path(worktree) / "spec" / "changes" / change_id / "tasks.md"
    if not tasks_md.exists():
        sys.exit(f"ERROR: tasks.md not found at {tasks_md}")
    res = parse_tasks(tasks_md)   # returns {total, completed, added, ...}

    # Retries / pass@k — from state.yaml (existing counters used by
    # compute-swe-metrics.sh today)
    ret = compute_retries(state)   # {retries_total, pass_at_1, pass_at_2,
                                   #  regressions, human_interventions}

    # Churn — git log / git diff --stat (run inside worktree)
    churn = run_git_churn(worktree, change_id)   # {files_changed, insertions,
                                                  #  deletions, total_commits,
                                                  #  rework_commits}

    # Reviews — scan state.yaml.step_history for review_score entries
    rev = extract_review_scores(state)  # {scores_list, avg}

    # Wall-clock from state.started_at / state.completed_at
    wc = wall_clock_minutes(state)

    # Upsert
    db = duckdb.connect(metrics_db_path())
    ensure_schema(db)
    upsert_feature_metrics(db, {
        "repo_root": repo_root, "change_id": change_id, "schema_name": schema,
        **res, **ret, **churn,
        "review_scores_json": json.dumps(rev["scores_list"]),
        "review_score_avg": rev["avg"],
        "wall_clock_minutes": wc,
        "source": f"ingest-feature-metrics@{utcnow_iso()}",
    })

    print(json.dumps({"feature_metrics_ingested": True}))
```

Helper functions (`parse_tasks`, `compute_retries`, `run_git_churn`,
`extract_review_scores`, `wall_clock_minutes`) are the **direct port** of the logic
already in `scripts/inline/compute-swe-metrics.sh`. They become unit-testable Python
instead of 736 lines of bash. This is where the bulk of the ~150-line sketch lives.

#### 5. `orchestrator metrics` subcommand (FR-5)

**File**: `config/scripts/orchestrator_next/cli.py` (or wherever `cost` subcommand
dispatches). New module: `config/scripts/orchestrator_next/metrics_report.py`.

CLI surface:
```
orchestrator metrics --change-id <slug> [--format json|yaml]
```

Signature:
```python
def aggregate_metrics(db, repo_root, change_id) -> dict:
    """Return a flat dict matching metrics-schema.md field registry."""
    cost_data = aggregate_feature(db, repo_root, change_id)   # from cost_report
    fm_row    = _fetch_feature_metrics(db, repo_root, change_id)  # may be None for spike
    fc_row    = _fetch_feature_complexity(db, repo_root, change_id)
    schema    = _resolve_schema(db, repo_root, change_id)      # from state.yaml or step_events

    return _project(cost_data, fm_row, fc_row, schema)
```

JSON shape (flat dict, keyed to metrics-schema.md — all present for feature/bugfix,
nulls for spike/autopilot per the Per-Schema Variants table):
```json
{
  "tokens": {"input": 123, "output": 45, "cache_creation": 67, "cache_read": 89, "total": 324},
  "cost": {
    "gross_usd": 0.1234, "net_usd": 0.0987,
    "model": "claude-sonnet-4-5",
    "pricing": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_creation": 3.75}
  },
  "turns": 42, "tool_calls": 312, "api_calls": 42,
  "wall_clock_minutes": 18.3,
  "resolution": {
    "tasks_total": 17, "tasks_planned": 17, "tasks_added": 0,
    "tasks_completed": 16, "tasks_failed": 1,
    "resolve_rate": 0.941, "pass_at_1": 0.88, "pass_at_2": 0.94,
    "regressions": 0, "regression_rate": 0.0
  },
  "retries": {"total": 2},
  "human_interventions": 0,
  "rework_commits": 3, "rework_rate": 0.15,
  "churn": {"files_changed": 12, "insertions": 340, "deletions": 120, "total_commits": 20},
  "review_scores": [8, 9, 9], "review_score_avg": 8.67,
  "lint_delta": 0,
  "category": "feature",
  "benchmarks": {
    "cost_per_task_usd": 0.0058, "cost_per_resolution_usd": 0.0062,
    "tokens_per_task": 19, "tokens_per_resolution": 20,
    "input_output_ratio": 4.22, "cache_hit_rate": 0.27
  },
  "per_agent_tokens": "{\"developer\": {\"total_tokens\": 120, ...}}",
  "per_agent_tools":  "{\"developer\": {\"Read\": 32, ...}}",
  "per_step": {"explore": {"total_tokens": 120, "tool_uses": 40, "duration_ms": 912000, "executions": 1}},
  "estimate_vs_actual": {...}   // OMITTED if route_preview estimate absent
}
```

`per_agent_tokens` and `per_agent_tools` remain stringified JSON scalars
(register-repo.sh reads them that way — NFR per discovery).

#### 6. `compute-swe-metrics.sh` rewrite (FR-6, FR-8)

**File**: `scripts/inline/compute-swe-metrics.sh` (rewrite, 736 → ~50 lines).

```bash
#!/usr/bin/env bash
# Thin projection over `orchestrator metrics --format json`.
set -euo pipefail

STATE_DIR="${1:?Usage: compute-swe-metrics.sh <state_dir>}"
STATE_YAML="$STATE_DIR/state.yaml"

REPO_ROOT=$(yq -r '.repo_root'  "$STATE_YAML")
CHANGE_ID=$(yq -r '.change_id'  "$STATE_YAML")

JSON=$("$ORCHESTRATOR_HOME/config/scripts/orchestrator.sh" metrics \
  --change-id "$CHANGE_ID" --format json)

TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Render: JSON → YAML under `metrics:` key, plus metrics.source provenance.
echo "metrics:"
echo "  source: \"duckdb@$TS\""
echo "$JSON" | yq -P 'with_entries(.key |= sub("^"; ""))' - | sed 's/^/  /'
```

(Exact sed/yq incantation finalized during T-14; intent: produce the same top-level
keys as the legacy script plus `metrics.source`.)

#### 7. `read-sub-state-metrics.sh` rewrite (FR-7)

**File**: `config/scripts/read-sub-state-metrics.sh` (rewrite, 80 → ~30 lines).

```bash
#!/usr/bin/env bash
set -euo pipefail

SLUG="${1:?Usage: read-sub-state-metrics.sh <slug>}"

# Resolve repo_root from the first location that has a state.yaml — matches
# current lookup order but the value is only used to pass to orchestrator.
for PATH_CAND in "$HOME/.workflows/$SLUG/state.yaml" \
                 "${REPO_ROOT:-}/spec/changes/archive/$SLUG/state.yaml"; do
  [[ -f "$PATH_CAND" ]] || continue
  STATE_FILE="$PATH_CAND"
  break
done
REPO_ROOT=$(yq -r '.repo_root' "$STATE_FILE")

JSON=$("$ORCHESTRATOR_HOME/config/scripts/orchestrator.sh" metrics \
  --change-id "$SLUG" --format json)

TOK=$(echo   "$JSON" | yq -p=json -r '.tokens.total          // 0')
DUR=$(echo   "$JSON" | yq -p=json -r '.wall_clock_minutes    // 0' | awk '{print int($1 * 60000)}')
CHURN=$(echo "$JSON" | yq -p=json -r '.churn.files_changed   // 0')

cat <<YAML
metrics:
  tokens:
    total: $TOK
  duration_ms: $DUR
  churn:
    files_changed: $CHURN
YAML
```

Narrow contract preserved (OQ-5).

#### 8. Complete-phase insertion (FR-9)

**File**: `config/workflows/_complete-phase.yaml`

```yaml
steps:
  - compute-prediction-accuracy
  - run-learn-cycle
  - mark-change-completed
  - ingest-feature-metrics        # ← new
  - compute-swe-metrics
  - archive-completed-change
  - remove-worktree
```

Test update (`config/tests/test-complete-phase-order.sh`): add
`POS_INGEST=$(get_pos "ingest-feature-metrics")` and two asserts:
`POS_MARK < POS_INGEST` and `POS_INGEST < POS_METRICS`. Add
`ingest-feature-metrics` to `REQUIRED_ORDER`.

#### 9. register-repo.sh invariant (FR-11)

**File**: `config/scripts/register-repo.sh` (around line 252 inside the
`step_history` loop).

Before the INSERT statement for each step_history row, add:
```bash
if [[ "$agent_val" != "null" && "$agent_val" != "inline" \
   && "$step_status_val" == "completed" \
   && "$total_tokens_val" == "null" ]]; then
  echo "WARN: skipping step_history row with missing usage: change=$q_change agent=$agent_val step=$step_id_val" >&2
  continue
fi
```

Simple guard; skips the bad row, logs to stderr. Does not abort overall ingest.

#### 10. Broken test paths (FR-12)

One task to fix all five files. Each `config/scripts/compute-swe-metrics.sh` →
`scripts/inline/compute-swe-metrics.sh`. No other changes.

### Data Flow

1. `orchestrator record` (existing) — every step upsert carries `turns` from
   `usage["turns"]` into `step_events.turns`.
2. `mark-change-completed` (existing) — writes `state.completed_at`.
3. `ingest-feature-metrics` (new) — reads state.yaml + tasks.md + runs
   `git log --format=%H` / `git diff --stat` in the worktree → one
   `feature_metrics` row.
4. `compute-swe-metrics` (thin) — `orchestrator metrics --change-id X` joins
   `step_events` + `feature_metrics` + `feature_complexity` + pricing, emits JSON,
   script projects to YAML under `metrics:` key + `metrics.source`.
5. `orchestrator record` captures the step_history entry.
6. `archive-completed-change` moves to `spec/changes/archive/<slug>/`.
7. `register-repo.sh` later ingests state.yaml + metrics; invariant rejects bad
   step_history rows.

### State Management

- `step_events`, `feature_metrics`, `feature_complexity`, `tool_calls` all in
  `metrics.duckdb` (existing). No new DB file.
- `state.yaml` still writes the `metrics:` block (for consumers: register-repo,
  telemetry skill, workflow-improver). Now a projection of DuckDB rather than a
  parallel source.
- Backfill: deliberately skipped. Archived state.yaml files keep their existing
  (possibly-zero) metrics.

### Error Handling

- **Migration errors**: DDL uses `IF NOT EXISTS`; `ALTER TABLE ADD COLUMN` is
  idempotent (the migration function checks for column presence first).
- **`ingest-feature-metrics` failure (UC-E1)**: non-zero exit, `orchestrator record`
  writes `status: failed` entry, dispatcher re-emits the step. Archive blocked
  until the operator fixes tasks.md / git / DuckDB.
- **Missing `turns` (UC-E2)**: JSONL parse failed → `usage["turns"]` absent → NULL
  in DuckDB column → `orchestrator metrics` returns `turns: null` → thin wrapper
  emits `turns: 0` (via yq `// 0` default).
- **Missing pricing entry**: fallback to `pricing.default` (same behavior as
  `record._compute_cost_usd`). Never crashes.
- **Slug guard**: `change_id` validated against `^[a-z0-9][a-z0-9-]*$` before every
  DuckDB operation (NFR-1).

## Constraints

- All SQL parameterised. No f-string interpolation of user data.
- `orchestrator record` remains the sole writer to `step_events`.
- `state.yaml.step_history` shape unchanged.
- `state.yaml.metrics` keys preserved (only additive: `metrics.source` new).
- `per_agent_tokens` and `per_agent_tools` remain stringified JSON scalars
  (register-repo.sh reads them via `yq -p=json`).
- JSONL format untouched.
- `step_events` column additions limited to `turns` this iteration.

## Trade-offs

- **Snapshot staleness**: if `ingest-feature-metrics.py` is later improved,
  archived state.yaml files keep the old snapshot. Acceptable — same as any
  serialised projection. Post-hoc queries via `orchestrator metrics` always reflect
  the current logic.
- **Complete-phase latency**: +1 step (~2s). Accepted; ingestion is cheap and runs
  once per completed feature.
- **Dual-read during transition**: The first feature completed after this ships
  gets both legacy and new data paths. Byte-compat test (AC-2) ensures no drift.

## Decisions

- **One new subcommand, not an alias** → `cost` stays narrow; `metrics` is broad →
  clearer CLI surface, separate semantic.
- **`ingest-feature-metrics` fails loud** → Block archive on failure → surfaces
  broken features instead of silently archiving zero-snapshots.
- **Spike complete-phase untouched** → Spike has no tasks.md → no resolution data
  to ingest → reduced phase file stays simple.
- **Pricing in YAML, not DuckDB** → One source of pricing truth → avoid schema
  churn when prices change → matches `record.py:_load_pricing` pattern.
- **`read-sub-state-metrics.sh` stays narrow** → `autopilot-session-rollup.sh`
  reads only three fields → expanding is a no-op; narrowing risk avoided.
- **Workflow artifacts in main repo `.state/`** → ISSUE-30 mitigation → unambiguous
  spawn contract → this spec/design/tasks written to main.
- **Register-repo invariant is skip-with-warn, not abort** → One bad row shouldn't
  fail the whole repo ingest → matches the `|| true` pattern already used in the
  script.

## Open Questions

None remaining. All seven OQs from `discovery.md` are resolved in spec.md §Decisions
and in this design. Any new ambiguity during implementation escalates via the
architect consult pathway (see tasks.md).

<!-- Format contract: contracts/artifact-formats.md § Design Format Contract -->
