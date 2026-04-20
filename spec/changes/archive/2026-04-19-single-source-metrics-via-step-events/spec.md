---
feature-id: single-source-metrics-via-step-events
linear-ticket: ~
---

# Specification: Single-Source Metrics via Step Events

## Motivation

Three readers compute feature metrics independently and disagree: `orchestrator cost`
queries DuckDB and is correct for the subset it covers; `scripts/inline/compute-swe-metrics.sh`
(736 lines) sums `state.yaml.step_history`, parses JSONL, walks git log, and reads
`tasks.md`; and `config/scripts/read-sub-state-metrics.sh` repeats a subset for autopilot.
Iter 1 of autopilot-2026-04-20 produced $0 in `state.yaml.metrics` and $0.246 in
`orchestrator cost` for the same feature. Aborted iter 2 proved a "thin wrapper" over
`orchestrator cost --format json` cannot close the gap — the CLI does not surface
`cache_creation_input_tokens`, `turns`, `gross_usd`, `cost.model`, or `cost.pricing.*`,
all required by `config/steps/contracts/metrics-schema.md`.

This feature makes DuckDB the **sole** aggregate source. Ingestion widens to capture
everything the consumer contract needs; wrapper scripts shell out to a new
`orchestrator metrics` subcommand and project the JSON to YAML. No parallel JSONL reads,
no parallel git walks, no parallel file-parsing at read time. State.yaml.metrics becomes
a snapshot of the DuckDB query.

## What Changes

- `step_events.turns BIGINT` column added; propagated from `jsonl_usage._aggregate()`.
- `cost_report._totals()` extended to project cache tokens, compute `gross_usd`, and
  return dominant model + pricing rates (via `pricing.yaml` dict lookup).
- New DuckDB table `feature_metrics` — one row per completed feature covering
  resolution, churn, retries, reviews.
- New step `ingest-feature-metrics` — populates `feature_metrics` from `tasks.md`,
  `git log`, and `state.yaml` counters. Runs between `mark-change-completed` and
  `compute-swe-metrics` in `_complete-phase.yaml`. **Fails loud** on error.
- New subcommand `orchestrator metrics --change-id X --format json` — joins
  `step_events + feature_metrics + feature_complexity` + pricing lookup.
- `compute-swe-metrics.sh` rewritten as thin YAML projection over the new subcommand
  (~50 lines from 736). Byte-compat with current output.
- `read-sub-state-metrics.sh` rewritten as thin projection (~30 lines from 80). Stays
  narrow — emits only `tokens.total`, `duration_ms`, `churn.files_changed`.
- `state.yaml.metrics.source: "duckdb@<timestamp>"` provenance field.
- `register-repo.sh` ingestion invariant: reject step_history rows where
  `agent != null AND status = completed AND total_tokens IS NULL` (except `agent: inline`).
- 5 broken test paths fixed (`config/scripts/compute-swe-metrics.sh` →
  `scripts/inline/compute-swe-metrics.sh`).

## Requirements

### Functional

1. **FR-1**: `step_events.turns` column exists after migration and carries the value
   from `jsonl_usage._aggregate()` through `upsert_step_event` (and `upsert_synthetic_event`).
2. **FR-2**: `orchestrator cost --change-id X --format json` `totals` block includes
   `cache_creation_input_tokens`, `cache_read_input_tokens`, `turns`, `gross_usd`,
   `model`, and `pricing.{input,output,cache_read,cache_creation}`.
3. **FR-3**: A `feature_metrics` DuckDB table exists with PK `(repo_root, change_id)`
   and columns for resolution (`tasks_total, tasks_completed, tasks_added, resolve_rate,
   pass_at_1, pass_at_2, regressions, regression_rate`), churn (`files_changed,
   insertions, deletions, total_commits, rework_commits, rework_rate`), retries
   (`retries_total, human_interventions`), reviews (`review_scores_json,
   review_score_avg`), timing (`wall_clock_minutes`), and audit (`source, computed_at`).
   DDL is idempotent (CREATE TABLE IF NOT EXISTS + migration guard pattern like
   upsert.py:190–215).
4. **FR-4**: Step `ingest-feature-metrics` exists at
   `config/steps/ingest-feature-metrics.yaml` + `scripts/inline/ingest-feature-metrics.py`.
   Inputs: state.yaml path, tasks.md path (derived from state), repo_root, change_id.
   Output: single upsert to `feature_metrics`. On failure (missing tasks.md for
   feature/bugfix, DuckDB write error, git command failure), the step exits non-zero
   and blocks archive. The step records to `step_history` via `orchestrator record`.
5. **FR-5**: `orchestrator metrics --change-id X --format json` exists and returns a
   JSON dict whose keys are flat and cover every field in the
   `config/steps/contracts/metrics-schema.md` field registry for the feature's schema.
   Output is stable (sorted keys, deterministic ordering).
6. **FR-6**: `scripts/inline/compute-swe-metrics.sh` is rewritten as a thin projection:
   shell out to `orchestrator metrics --change-id X --format json`, render as YAML
   matching the metrics-schema.md shape, emit to stdout. No JSONL parsing, no
   `git log`, no `tasks.md` reading inside the script.
7. **FR-7**: `config/scripts/read-sub-state-metrics.sh` is rewritten as a thin
   projection: shell out to `orchestrator metrics`, emit the three narrow fields
   consumed by `autopilot-session-rollup.sh` (`tokens.total`, `duration_ms`,
   `churn.files_changed`). Output contract stays narrow.
8. **FR-8**: `state.yaml.metrics.source` is populated as `"duckdb@<ISO8601>"` by the
   rewritten `compute-swe-metrics.sh`.
9. **FR-9**: `_complete-phase.yaml` step order is exactly:
   `compute-prediction-accuracy → run-learn-cycle → mark-change-completed →
   ingest-feature-metrics → compute-swe-metrics → archive-completed-change →
   remove-worktree`. The ordering test at `config/tests/test-complete-phase-order.sh`
   asserts `ingest-feature-metrics` between `mark-change-completed` and
   `compute-swe-metrics`.
10. **FR-10**: `_complete-phase-spike.yaml` is **not** modified. Spike has no tasks.md
    and runs a reduced complete phase; `ingest-feature-metrics` does not run for spike
    or autopilot schemas.
11. **FR-11**: `register-repo.sh` rejects (skips row + logs to stderr) any
    `step_history` row where `agent != null AND agent != "inline" AND status =
    "completed" AND total_tokens IS NULL`.
12. **FR-12**: The 5 broken test paths are fixed:
    `config/tests/test-compute-swe-metrics-ordering.sh:14`,
    `config/tests/test-per-agent-tokens-coverage.sh:13`,
    `config/tests/test-compute-swe-metrics-per-step.sh:22`,
    `config/scripts/__tests__/compute-swe-metrics.test.sh:10`,
    `config/scripts/__tests__/compute-swe-metrics-cost.test.sh:11`.

### Non-Functional

1. **NFR-1**: All DuckDB writes use parameterised `duckdb.execute(sql, params)`; no
   string interpolation of user data. `change_id` passes the slug guard
   `^[a-z0-9][a-z0-9-]*$` before any INSERT.
2. **NFR-2**: Schema migrations (new column, new table) are idempotent — safe to
   call every `orchestrator next`. No external migration tool.
3. **NFR-3**: `compute-swe-metrics.sh` output is **byte-compatible** with the
   existing output for any feature that has complete DuckDB data (token/cost/turns
   identical, resolution/churn identical within floating-point tolerance).
4. **NFR-4**: `orchestrator metrics` JSON shape is considered a public contract;
   changes require a regression test update.
5. **NFR-5**: `ingest-feature-metrics` completes in < 3s for a typical feature
   (bounded by `git log` + tasks.md parse, no network).

## Architecture

Four touchpoints in the orchestrator CLI:

| Layer | File | Change |
|---|---|---|
| Storage | `config/scripts/orchestrator_next/upsert.py` | ADD COLUMN `turns`; extend `_migrate_step_events`; add `turns` param to `_INSERT_OR_REPLACE` and `upsert_synthetic_event`. Add `_DDL_FEATURE_METRICS` + `_migrate_feature_metrics` + `upsert_feature_metrics()`. |
| Aggregation | `config/scripts/orchestrator_next/cost_report.py` | Widen `_totals()` SELECT to include cache_creation/cache_read/turns; compute `gross_usd` in Python (unit rates × token counts); add dominant-model resolution via GROUP BY; attach `pricing.*` from `pricing.yaml` dict lookup. New module `metrics_report.py` (or function in `cost_report.py`) composing `aggregate_feature` + `feature_metrics` + schema variant handling → flat JSON dict keyed to `metrics-schema.md`. |
| CLI | `config/scripts/orchestrator_next/cli.py` (or main dispatch) | New `metrics` subcommand parallel to `cost`. |
| Ingest step | `config/steps/ingest-feature-metrics.yaml` + `scripts/inline/ingest-feature-metrics.py` | New. Reads tasks.md + git log + state.yaml; upserts to `feature_metrics`. |
| Wrappers | `scripts/inline/compute-swe-metrics.sh`, `config/scripts/read-sub-state-metrics.sh` | Rewritten thin (50 / 30 lines). |
| Workflow | `config/workflows/_complete-phase.yaml` | Insert `ingest-feature-metrics` at position 4 (0-indexed). |
| Tests | `config/tests/test-complete-phase-order.sh` + 5 path-broken tests | Update ordering asserts; fix paths. |
| Ingest guard | `config/scripts/register-repo.sh` | Invariant check around `step_history` loop (line ~252). |

Data flow at complete phase:

```
mark-change-completed          (sets completed_at in state.yaml)
        │
        ▼
ingest-feature-metrics         (Python step)
  read state.yaml              (step_history, retries, flags, route_preview)
  read tasks.md                (task counts, completion markers)
  run git log / git diff       (churn)
  UPSERT feature_metrics       (single row)
  record step_history via `orchestrator record`
        │
        ▼
compute-swe-metrics            (thin bash wrapper)
  orchestrator metrics --change-id X --format json
    ├─ aggregate_feature(step_events)      → tokens, cost, turns, per_agent_tokens
    ├─ SELECT from feature_metrics         → resolution, churn, retries, reviews
    ├─ SELECT from feature_complexity      → complexity bucket
    └─ load pricing.yaml                   → cost.pricing.* for dominant model
  project JSON → YAML (yq)
  inject into state.yaml.metrics (via orchestrator record outputs)
        │
        ▼
archive-completed-change
```

## Test Strategy

### Test File Paths

| Component | Test file |
|---|---|
| `step_events.turns` column + migration | `config/scripts/orchestrator_next/__tests__/test_upsert_turns.py` |
| `_totals()` cache/gross_usd/pricing projection | `config/scripts/orchestrator_next/__tests__/test_totals_wide.py` |
| `feature_metrics` DDL + migration | `config/scripts/orchestrator_next/__tests__/test_feature_metrics_ddl.py` |
| `orchestrator metrics` subcommand | `config/tests/test-orchestrator-metrics-json-shape.sh` |
| `ingest-feature-metrics.py` step | `config/scripts/__tests__/test-ingest-feature-metrics.sh` |
| Complete-phase ordering | `config/tests/test-complete-phase-order.sh` (existing — extended) |
| `compute-swe-metrics.sh` byte-compat | `config/scripts/__tests__/compute-swe-metrics-projection.test.sh` |
| `read-sub-state-metrics.sh` narrow contract | `config/scripts/__tests__/read-sub-state-metrics.test.sh` |
| `register-repo.sh` invariant | `config/tests/test-register-repo-usage-invariant.sh` |
| End-to-end | `config/tests/test-metrics-pipeline-integration.sh` |

### Coverage Targets

Project default — 90% on Python modules (`upsert.py`, `cost_report.py`, `metrics_report.py`
if added, `ingest-feature-metrics.py`). Bash scripts checked for exit-code / output
shape.

### Key Test Scenarios

- **TDD RED first**: every implementation task has a preceding failing test task.
- **Seeded DuckDB fixture**: one feature with full `step_events` population, exercised
  end-to-end through `ingest-feature-metrics` + `orchestrator metrics` + byte-compat
  comparison against a golden YAML snapshot.
- **JSONL-parse-failure path (UC-E2)**: turns column is NULL → `orchestrator metrics`
  returns null → `compute-swe-metrics.sh` emits `turns: 0`.
- **Ingest failure path (UC-E1)**: missing tasks.md → `ingest-feature-metrics` exits
  non-zero → dispatcher blocks on `compute-swe-metrics` (retry path).
- **Migration idempotency**: call `ensure_schema` twice — no errors, no duplicate
  migrations.

## Acceptance Criteria

- **AC-1**: Given a completed feature with populated DuckDB data, when
  `orchestrator metrics --change-id X --format json` is invoked, then the returned
  JSON contains every field required by `config/steps/contracts/metrics-schema.md`
  for that schema's variant (feature/bugfix/spike/autopilot). [traces: UC-3]
- **AC-2**: Given the same feature, when `compute-swe-metrics.sh` runs before the
  change and after the change (rewrite), then the emitted YAML `metrics:` block has
  the same top-level keys and the same token/cost/turns/churn values (byte-compatible
  on integers; ≤1e-4 USD tolerance on floats). [traces: UC-1]
- **AC-3**: Given a feature mid-complete-phase with `tasks.md` deleted, when
  `ingest-feature-metrics` runs, then it exits non-zero and the dispatcher does NOT
  advance to `archive-completed-change`. [traces: UC-E1]
- **AC-4**: Given `_complete-phase.yaml` with `ingest-feature-metrics` inserted,
  when `test-complete-phase-order.sh` runs, then it asserts
  `mark-change-completed < ingest-feature-metrics < compute-swe-metrics` and passes.
  [traces: UC-1]
- **AC-5**: Given a freshly-completed feature's state.yaml at
  `spec/changes/archive/<slug>/state.yaml`, when `register-repo.sh` ingests it,
  then no `step_history` row with `agent != inline AND status = completed AND
  total_tokens IS NULL` is INSERTed (the invariant logs + skips). [traces: FR-11]
- **AC-6**: Given a feature where JSONL enrichment succeeded, when the dispatcher
  calls `orchestrator cost --change-id X --format json` after archive, then
  `totals.cache_creation_input_tokens` and `totals.turns` are present and non-zero.
  [traces: UC-2]

## Alternatives Considered

**Approach A (rejected): Thin wrapper over `orchestrator cost --format json`.**
The iter-2 architect proposed rewriting `compute-swe-metrics.sh` to shell out to
`orchestrator cost` and project the result. `_totals()` at `cost_report.py:62–95`
does not return `cache_creation_input_tokens`, `turns`, `gross_usd`, `cost.model`, or
`cost.pricing.*` — all required by `metrics-schema.md`. The workaround was a hybrid
keeping JSONL as a second read source inside the wrapper — the exact parallel-read
pattern this feature must eliminate. Reviewer scored 8/10 and flagged the
contradiction. See `.state/autopilot/archive/aborted/2026-04-20-single-source-metrics-via-step-events/retro.md`
§ISSUE-32.

**Approach C (rejected): Leave existing scripts, add DuckDB as optional supplemental source.**
Doesn't fix the $0 vs $0.246 divergence and leaves 736 lines of JSONL-parsing shell
as canonical.

**Approach B (chosen): Widen DuckDB + new table + new subcommand + thin wrappers.**
Only design that (a) closes the CLI gap at the source, (b) eliminates all parallel
reads, (c) preserves the `state.yaml.metrics` wire contract byte-for-byte.

## Impact

- **Breaking**: None. Wire contracts for `state.yaml.metrics` and
  `orchestrator cost --format json` are preserved as supersets (new keys added, none
  removed).
- **Migration**: `step_events.turns` column added on first `ensure_schema()` call;
  `feature_metrics` table created likewise. Idempotent. No user action.
- **Backfill**: Deliberately not in scope. Archived state.yaml files with $0 metrics
  remain $0 — only new completions get correct values.
- **Affected areas**: Complete-phase ordering (feature/bugfix only), telemetry
  archives, `register-repo.sh` ingest.

## Decisions (Open Questions Resolved)

- **OQ-1 → `metrics` is a separate subcommand from `cost`**: `cost` stays narrowly
  about token/cost totals; `metrics` is the broader projection that also covers
  resolution/churn/retries/reviews. CLI surface: `orchestrator metrics
  --change-id <slug> [--format json|yaml]`. Rationale: different semantics, separate
  subcommand is clearer and matches backlog recommendation.
- **OQ-2 → `ingest-feature-metrics` fails loud**: Non-zero exit blocks archive.
  Operator must investigate. Rationale: better to surface a broken feature than to
  silently archive a zero-snapshot (which is how we got here).
- **OQ-3 → Complete-phase ordering enforced by test**: Both `_complete-phase.yaml`
  and `test-complete-phase-order.sh` are updated in the same task. The test adds an
  assertion `POS_INGEST > POS_MARK && POS_INGEST < POS_METRICS`.
- **OQ-4 → Spike keeps its own phase file unchanged**: `ingest-feature-metrics` runs
  for `feature` and `bugfix` only. Spike + autopilot have no tasks.md → no resolution
  fields to ingest. `_complete-phase-spike.yaml` is untouched. For spike,
  `compute-swe-metrics.sh` still shells out to `orchestrator metrics`, but the
  `feature_metrics` row is absent — the subcommand returns null for resolution fields,
  matching the metrics-schema.md null contract (`~`).
- **OQ-5 → `read-sub-state-metrics.sh` stays narrow**: Emits only `tokens.total`,
  `duration_ms`, `churn.files_changed`. Verified against `autopilot-session-rollup.sh`
  which only reads those three fields. Expanding would be a no-op and risks breaking
  the `yq` paths in the rollup.
- **OQ-6 → Workflow artifacts live in `$REPO_ROOT/.state/<slug>/`**: **All** workflow
  artifacts (spec.md, design.md, tasks.md, review-*.md, retro.md) live in the **main
  repo's** `.state/<slug>/`, never in the worktree's `.state/<slug>/`. Only production
  code changes land in the worktree. Agent prompts must make this explicit; see
  ISSUE-30 mitigation (this spec itself is written to main, not worktree).
- **OQ-7 → Pricing loaded from `pricing.yaml` at query time**: There is no
  `agent_pricing` DuckDB table (grep confirms — only prose references in backlog).
  Pricing lives in `config/pricing.yaml`. `orchestrator metrics` loads it via the
  same `_load_pricing()` pattern as `record.py:53–60` (lru_cached per process) and
  attaches `cost.pricing.*` rates keyed by the dominant model returned from
  `step_events.model`. Adding an `agent_pricing` DuckDB table is deliberately out of
  scope — one source of pricing truth is enough.

## Out of Scope

- History repair for archived state.yaml files with $0 metrics (retired decision: no backfill).
- Changes to `orchestrator record` beyond the `turns` passthrough.
- JSONL format changes.
- `step_events` column additions beyond `turns`.
- Any UI.
- `agent_pricing` DuckDB table (pricing stays in YAML).
- Autopilot/spike changes to ingest step.

## Known Limitations

- `orchestrator metrics --change-id X` exits non-zero with `error: no events for change_id=X` when the change_id has no rows in `step_events` (e.g., historical archives created before DuckDB ingest was enabled, or features from repos that haven't been registered). Forward features (post-ship) populate step_events via `orchestrator record` and work end-to-end. Graceful no-data handling (return structured `{status: "no_data"}` with exit 0) is a follow-up — tracked in backlog as `metrics-no-data-graceful`.

<!-- Format contract: contracts/artifact-formats.md § Specification Format Contract -->
