---
feature-id: single-source-metrics-via-step-events
linear-ticket: ~
---

# Discovery Brief: single-source-metrics-via-step-events

## Problem Statement

Three readers today compute feature metrics independently and produce divergent numbers:
`orchestrator cost --change-id` queries `step_events` and is correct for the token/cost
subset it covers; `scripts/inline/compute-swe-metrics.sh` (736 lines) sums `state.yaml.step_history[].usage`,
parses JSONL, walks git log, and reads `tasks.md` to write `state.yaml.metrics`; and
`config/scripts/read-sub-state-metrics.sh` repeats a subset of that pattern for autopilot
iteration metrics. The deviation is live: iter 1 of autopilot-2026-04-20 produced $0 in
`state.yaml.metrics` and $0.246 in `orchestrator cost` for the same feature. Aborted iter 2
(2026-04-20) proved that simply wrapping `compute-swe-metrics.sh` around the CLI doesn't
close the gap: the CLI's `_totals()` projection does not surface `cache_creation_input_tokens`,
`turns`, `gross_usd`, `cost.model`, or `pricing.*` — all required by
`config/steps/contracts/metrics-schema.md`. The real fix is: widen the `orchestrator cost`
projection, add a new `feature_metrics` DuckDB table for resolution/churn/review data, expose
everything through a new `orchestrator metrics` subcommand, and rewrite the wrapper scripts
as thin SQL projections. Full scope is in `spec/changes/backlog.md` lines 22–117.

## Current State Survey

### `orchestrator cost` JSON output — gap verification

`_totals()` (cost_report.py:62–95) SELECTs only:
  `cost_usd, input_tokens, output_tokens, duration_ms, step_count, rework_ratio`

Confirmed absent from `totals` block (verified live against `live-telemetry-and-repeat-until-enforcement`):
  - `cache_creation_input_tokens` — ABSENT in totals projection
  - `cache_read_input_tokens` — ABSENT in totals projection
  - `turns` — ABSENT in totals projection
  - `gross_usd` — ABSENT in totals projection
  - `cost.model` — ABSENT from totals (present nowhere in JSON output)
  - `cost.pricing.*` — ABSENT from JSON output

Key finding: `step_events` already stores `cache_creation_input_tokens` (upsert.py:45) and
`cache_read_input_tokens` (upsert.py:44). The gap is purely in the SQL SELECT inside
`_totals()` (cost_report.py:62–72), NOT in the storage layer. `turns` is also computed by
`jsonl_usage._aggregate()` (jsonl_usage.py:55,77,101) but is dropped before DuckDB upsert.

### `step_events` schema — current columns

From upsert.py:30–52:
  Dimension keys: `repo_root, change_id, phase, step_id, attempt, agent_name, agent_id, status`
  Timestamps: `started_at, ended_at, duration_ms`
  Token/cost: `model, input_tokens, output_tokens, cache_read_input_tokens,
               cache_creation_input_tokens, cost_usd`
  JSON payloads: `tool_calls_json, artifacts_json, escalation_json`
  Audit: `upserted_at`
  Missing: `turns` — not present, not passed through; needs ADD COLUMN + upsert passthrough

Note: the contracts/metrics-schema.md uses OTel-style column names (`gen_ai_usage_input_tokens`
etc.) in its Step Events Schema section, but upsert.py uses shorter names. These are the same
physical columns; the contract doc and code are aligned.

### `scripts/inline/compute-swe-metrics.sh` — input/output contract

Inputs (736 lines total):
  - `$1` = `<state_dir>` (required positional)
  - `<state_dir>/state.yaml` — reads: `step_history[].usage.*`, `step_history[].status`,
    `step_history[].agent`, `step_history[].tools`, `step_history[].started_at`,
    `step_history[].completed_at`, `schema`, `started_at`, `completed_at`,
    `flags.light`, `route_preview.estimate.*`, retry counters
  - `<state_dir>/tasks.md` — reads task completion markers (`[x]`, `[ ]`)
  - Claude Code session JSONL files — looked up via git repo slug + timestamp window
    (lines 69–162); provides `cache_creation`, `cache_read`, `turns`, `model`
  - `git log` — for churn: `files_changed`, `insertions`, `deletions`, `rework_commits`
  - `$ORCHESTRATOR_HOME/config/pricing.yaml` — for rate lookup

Outputs: YAML metrics block written to stdout (caller injects into state.yaml).
  All fields listed in `config/steps/contracts/metrics-schema.md` field registry.
  Specifically: `tokens.*`, `cost.*` (including `gross_usd`, `net_usd`, `model`, `pricing.*`),
  `turns`, `tool_calls`, `api_calls`, `wall_clock_minutes`, `resolution.*`, `retries.*`,
  `human_interventions`, `rework_commits`, `rework_rate`, `churn.*`, `review_scores`,
  `review_score_avg`, `lint_delta`, `category`, `benchmarks.*`, `estimate_vs_actual.*`,
  `per_tool_uses`, `per_agent_tokens`, `per_agent_tools`, `per_step.*`

### `config/scripts/read-sub-state-metrics.sh` — input/output contract

Inputs (~80 lines):
  - `$1` = `<slug>` (required positional)
  - Locates state.yaml from: `$HOME/.workflows/<slug>/state.yaml` then
    `$REPO_ROOT/spec/changes/archive/<slug>/state.yaml` (ISSUE-26: path bug here)
  - Reads: `step_history[].usage.total_tokens`, `step_history[].usage.duration_ms`,
    `metrics.churn.files_changed`

Outputs: narrow YAML block:
  `metrics.tokens.total`, `metrics.duration_ms`, `metrics.churn.files_changed`
  (Notably: does NOT read cost, turns, resolution — much narrower than compute-swe-metrics)

### `feature_metrics` table existence

Confirmed absent. Grep across entire codebase finds only backlog.md references.
No DDL, no DDL in upsert.py, no query in cost_report.py.

### `jsonl_usage._aggregate()` — turns extraction

Confirmed: jsonl_usage.py:55 initializes `turns = 0`; line 77 increments per assistant turn;
line 101 sets `result["turns"] = turns`. The value is computed but never propagated into
the upsert flow. Backlog claim is verified.

### Complete-phase step ordering — current

From `config/workflows/_complete-phase.yaml`:
```
steps:
  - compute-prediction-accuracy
  - run-learn-cycle
  - mark-change-completed
  - compute-swe-metrics       ← new ingest-feature-metrics must precede this
  - archive-completed-change
  - remove-worktree
```

`mark-change-completed` must run before `compute-swe-metrics` (step contract documented at
config/steps/mark-change-completed.yaml:14). `ingest-feature-metrics` must run before
`compute-swe-metrics` so DuckDB is populated before the snapshot is taken. Insertion point
is between `mark-change-completed` and `compute-swe-metrics`. The existing ordering test
at `config/tests/test-complete-phase-order.sh` will need a new assertion for
`ingest-feature-metrics` position.

### Broken test paths (5 confirmed)

References to `config/scripts/compute-swe-metrics.sh` (wrong path; canonical is
`scripts/inline/compute-swe-metrics.sh`):
  - config/tests/test-compute-swe-metrics-ordering.sh:14
  - config/tests/test-per-agent-tokens-coverage.sh:13
  - config/tests/test-compute-swe-metrics-per-step.sh:22
  - config/scripts/__tests__/compute-swe-metrics.test.sh:10
  - config/scripts/__tests__/compute-swe-metrics-cost.test.sh:11

These silently SKIP today because the script isn't found at that path.

## Integration Points — Who Reads What

### `register-repo.sh`
- register-repo.sh:283 — reads `metrics.per_agent_tokens` (JSON string), iterates keys,
  writes to `per_agent_metrics` DuckDB table (total_tokens, cost_usd, tool_uses, duration_ms, steps)
- register-repo.sh:308 — reads `metrics.per_agent_tools` (JSON string), iterates,
  writes to `per_agent_tool_uses` DuckDB table
- register-repo.sh:327 — reads `metrics.per_tool_uses` (JSON string)
- register-repo.sh:252 — reads `step_history[].usage.total_tokens` for step_history table

Wire contract: `per_agent_tokens` and `per_agent_tools` must remain stringified JSON in
the `state.yaml.metrics` block. Format cannot change.

### `skills/telemetry/SKILL.md`
Reads from archived state.yaml files (skills/telemetry/SKILL.md:66–84):
  `metrics.cost.net_usd`, `metrics.tokens.total`, `metrics.benchmarks.cache_hit_rate`,
  `metrics.wall_clock_minutes`, `metrics.review_score_avg`, `metrics.resolution.pass_at_1`,
  `metrics.rework_rate`, `metrics.resolution.regression_rate`, `metrics.turns`,
  `metrics.tool_calls`, `metrics.retries.total`

Null contract: skills/telemetry/SKILL.md:143 — "Use null values gracefully."

### `agents/workflow-improver.md`
Reads from archived state.yaml (workflow-improver.md:146–166):
  `metrics.resolution.*` (fields marked as YAML null for spike/autopilot),
  `metrics.category` (to group by schema before cross-schema comparisons)

### `orchestrator cost` CLI consumers
Current output shape (`totals` top-level keys): `cost_usd, input_tokens, output_tokens,
duration_ms, step_count, rework_ratio`. Does NOT include any of the metrics-schema.md
required fields beyond basic token/cost — see gap list above.

## Constraints

1. `orchestrator record` remains the sole writer for `step_events`. Do not add writers.
2. `state.yaml.step_history` shape is unchanged — it stays as dispatcher memory.
3. JSONL format is Anthropic's artifact — do not touch.
4. `step_events` DuckDB schema changes limited to: ADD COLUMN `turns BIGINT` only.
5. `state.yaml.metrics` block shape must remain byte-compatible with all consumers listed
   above. The `metrics.source` provenance field is additive (safe to add).
6. `per_agent_tokens` and `per_agent_tools` must remain stringified JSON scalars in YAML.
7. `register-repo.sh` must NOT be broken by any rewrite — it reads `metrics.*` directly
   from archived YAML and inserts into its own DuckDB tables.
8. Complete-phase ordering invariant: `mark-change-completed` < `ingest-feature-metrics`
   < `compute-swe-metrics` < `archive-completed-change`.

## Build-or-Reuse Decisions

| Piece | Decision | Rationale |
|---|---|---|
| `feature_metrics` DuckDB table | Build (new DDL) | Does not exist; confirmed by codebase grep |
| `orchestrator metrics` subcommand | Build (new CLI entry point) | Aggregates `step_events + feature_metrics + feature_complexity + agent_pricing` — no current subcommand does this |
| `step_events.turns` column | Build (ADD COLUMN + passthrough) | Column absent confirmed; `_aggregate()` computes turns but drops them before upsert |
| `_totals()` cache/model/pricing projection | Build (SQL + Python patch) | Column exists in storage; gap is purely in SELECT (cost_report.py:62–72) |
| `ingest-feature-metrics` step | Build (new Python step ~150 lines) | No equivalent step exists |
| `compute-swe-metrics.sh` rewrite | Rewrite-in-place (keep step ID, shrink body) | Step wiring stays; body replaced by CLI shell-out |
| `read-sub-state-metrics.sh` rewrite | Rewrite-in-place (keep path, shrink body) | Same pattern; path-lookup bug (ISSUE-26) disappears when DuckDB is keyed by change_id |

"Thin wrapper over orchestrator cost" hybrid approach: REJECTED. See Alternatives Considered.

## Alternative Approaches Considered

### Approach A (Rejected): Thin wrapper over `orchestrator cost --format json`

The original backlog entry and iter-2 architect design: rewrite `compute-swe-metrics.sh`
to shell out to `orchestrator cost --format json` and project the result as YAML.
Rejected because `_totals()` at cost_report.py:62–95 does not return `cache_creation_input_tokens`,
`turns`, `gross_usd`, `cost.model`, or `cost.pricing.*` — all required by metrics-schema.md
for every schema including feature/bugfix. The architect's workaround was a hybrid that kept
JSONL as a second read source inside the wrapper — the exact parallel-read pattern the feature
was supposed to eliminate. Reviewer scored it 8/10 but flagged the core contradiction.
Full detail: `.state/autopilot/archive/aborted/2026-04-20-single-source-metrics-via-step-events/retro.md`
ISSUE-32 (retro.md:64–101).

### Approach B (Chosen): Widen DuckDB + new table + new subcommand + thin wrappers

Extend `_totals()` to project the stored `cache_creation/read` columns, propagate `turns`
from `_aggregate()` through upsert, add `feature_metrics` for resolution/churn/review,
expose via `orchestrator metrics` subcommand, then rewrite both wrapper scripts to shell
out to the new subcommand. DuckDB becomes the sole runtime source; wrappers become projections.
Eliminates all parallel reads. Sized at ~560 lines added / ~900 deleted.

### Approach C (Not recommended): Leave existing scripts, add DuckDB as optional source

Keep `compute-swe-metrics.sh` as-is; add DuckDB reads as supplemental for cross-feature
queries only. Rejected because it doesn't fix the $0 vs $0.246 divergence (the core
motivation), and leaves 736 lines of JSONL-parsing shell script as the canonical source.

## Personas & Actors

- **Orchestrator driver** — reads `state.yaml.metrics` at complete phase to confirm snapshot
- **Telemetry skill** — reads archived `state.yaml.metrics` for reporting
- **Workflow-improver agent** — reads `metrics.resolution.*` and `metrics.category` for improvements
- **register-repo.sh** — reads `metrics.per_agent_tokens`, `metrics.per_agent_tools` to populate DuckDB
- **`orchestrator cost` CLI** — queries `step_events` at any time
- **`orchestrator metrics` CLI (new)** — joins `step_events + feature_metrics + feature_complexity + agent_pricing`

## Use Cases

UC-1: Complete-phase snapshot — orchestrator runs `ingest-feature-metrics` step at start of
  complete phase so that `compute-swe-metrics` (now thin wrapper) finds DuckDB populated and
  emits a complete `state.yaml.metrics` block with no $0 values.

UC-2: Post-hoc cost query — developer runs `orchestrator cost --change-id X --format json`
  after archive and sees `cache_creation_input_tokens` and `turns` in totals.

UC-3: Cross-feature query — developer runs `orchestrator metrics --repo --since 2026-01-01`
  and gets resolution rates, churn, and cost from one DuckDB join instead of scanning YAML.

UC-E1: `ingest-feature-metrics` failure mid-complete-phase — step fails loud (non-zero exit),
  blocks archive. No silent zero-snapshot. Operator must investigate before re-running.

UC-E2: `turns` absent from JSONL (JSONL parse fails) — upsert writes NULL for `turns` column;
  `orchestrator metrics` returns NULL for turns field; `compute-swe-metrics` thin wrapper emits
  `turns: 0` rather than crashing.

## Scope

### In Scope

- ADD COLUMN `turns BIGINT` to `step_events`; propagate from `_aggregate()` through upsert
- Extend `_totals()` to SELECT `cache_creation_input_tokens`, `cache_read_input_tokens`, plus
  compute `gross_usd` (at full rates) and return dominant `model` + `pricing.*` from agent_pricing join
- New DuckDB table `feature_metrics` (DDL + migration) — columns as specified in backlog lines 47
- New step `ingest-feature-metrics.py` (~150 lines Python)
- New subcommand `orchestrator metrics --change-id X --format json`
- Rewrite `scripts/inline/compute-swe-metrics.sh` as thin projection (~50 lines)
- Rewrite `config/scripts/read-sub-state-metrics.sh` as thin projection (~30 lines)
- `metrics.source: "duckdb@<timestamp>"` provenance field in `state.yaml.metrics`
- Register-repo.sh ingestion invariant: reject step_history rows where `agent \!= null AND
  status = completed AND total_tokens IS NULL` (except `agent: inline`)
- Fix 5 broken test paths (config/scripts/compute-swe-metrics.sh → scripts/inline/...)
- JSON-shape regression test for `orchestrator metrics --format json`
- Integration test: seeded DuckDB ingest + read-back for one feature

### Out of Scope

- History repair for archived state.yaml files with $0 metrics (retired entry; decision: no backfill)
- Changes to `orchestrator record` command beyond what propagates `turns` to DuckDB
- JSONL format changes
- `step_events` column additions beyond `turns`
- Any UI

## Open Questions for the Architect

OQ-1: **`orchestrator metrics` vs `orchestrator cost` subcommand relationship** — should
  `orchestrator metrics` be a new top-level subcommand or an extension of `orchestrator cost`
  with `--scope all`? Backlog recommends separate (different semantic: cost=narrow, metrics=broad).
  Architect to confirm and document the CLI surface.

OQ-2: **`ingest-feature-metrics` failure policy** — fail loud (blocks archive) or write
  `feature_metrics.status: "no_data"` and continue? Backlog recommends fail loud. Architect
  to make this call explicit in spec.md.

OQ-3: **Complete-phase ordering enforcement** — the existing ordering test
  (`config/tests/test-complete-phase-order.sh`) pins 6 steps with explicit position checks.
  Adding `ingest-feature-metrics` between `mark-change-completed` and `compute-swe-metrics`
  changes relative positions. Both the workflow YAML and the test need updating. Architect must
  confirm the final ordered list and update the test to assert `ingest-feature-metrics` position.

OQ-4: **Spike complete-phase** — `config/workflows/_complete-phase-spike.yaml` has only
  `[compute-swe-metrics, archive-completed-change]`. Does `ingest-feature-metrics` run
  for spike workflows? Spike has no tasks.md, so resolution fields would be null. Architect
  to decide: include with null-graceful handling, or spike keeps its own phase file unchanged.

OQ-5: **`read-sub-state-metrics.sh` output contract for autopilot** — current output is narrow
  (`tokens.total`, `duration_ms`, `churn.files_changed`). After rewrite, does it expand to
  emit the full metrics contract or stay narrow? The autopilot consumer of this output
  (`config/scripts/autopilot-session-rollup.sh`) must be surveyed before rewrite.

OQ-6: **WORKFLOW_STATE_DIR / ORCHESTRATOR_WORKFLOW_DIR ambiguity (ISSUE-30 / ISSUE-31)** —
  `ORCHESTRATOR_WORKFLOW_DIR` was unset at discoverer spawn time. `WORKFLOW_STATE_DIR` was
  also unset. This discovery.md was written to the main repo's `.state/` (
  `/Users/spidey/code/orchestrator/.state/single-source-metrics-via-step-events/discovery.md`)
  per the ISSUE-30 convention: "workflow artifacts ALWAYS live in `$REPO_ROOT/.state/<slug>/`".
  Architect must either (a) enforce the convention in agent prompts or (b) define and
  document `WORKFLOW_STATE_DIR` as always pointing at main `.state/`. The spawn contract
  must be unambiguous before architect spawns developer.

OQ-7: **`_totals()` pricing join** — `gross_usd` requires rates from `agent_pricing` table.
  The `step_events` table stores `model` (VARCHAR). Verify that `agent_pricing` (or
  equivalent in DuckDB) is queryable for rate lookup at `orchestrator cost` time. If not,
  `_totals()` may need to read `config/pricing.yaml` as a Python dict like `record.py:53–113`.

## Technical Context

| File | Role |
|---|---|
| config/scripts/orchestrator_next/cost_report.py:62–95 | `_totals()` — SQL projection gap; patch here |
| config/scripts/orchestrator_next/upsert.py:30–52 | `step_events` DDL; ADD COLUMN `turns` here |
| config/scripts/orchestrator_next/upsert.py:113–136 | INSERT_OR_REPLACE; add `turns` param |
| config/scripts/orchestrator_next/upsert.py:190–215 | Migration guard pattern for ADD COLUMN |
| config/scripts/orchestrator_next/jsonl_usage.py:45–106 | `_aggregate()` — already computes `turns`; wire into upsert |
| config/scripts/orchestrator_next/record.py:53–134 | Pricing lookup; pattern for pricing.yaml reads |
| scripts/inline/compute-swe-metrics.sh | 736-line script to replace; preserves step_id/contract |
| config/scripts/read-sub-state-metrics.sh | ~80-line script; ISSUE-26 path bug disappears in rewrite |
| config/workflows/_complete-phase.yaml | Insert `ingest-feature-metrics` between mark-change-completed:19 and compute-swe-metrics:20 |
| config/workflows/_complete-phase-spike.yaml | Decide whether ingest-feature-metrics runs for spike |
| config/tests/test-complete-phase-order.sh | Ordering test; needs new assertion for ingest-feature-metrics |
| config/steps/contracts/metrics-schema.md | Consumer contract; all fields are required output |
| config/steps/mark-change-completed.yaml | Must run before ingest-feature-metrics (documented at line 14) |
| config/scripts/register-repo.sh:283–323 | Reads per_agent_tokens, per_agent_tools, per_tool_uses from metrics |
| skills/telemetry/SKILL.md:66–84 | Reads 11 metrics fields from archived state.yaml |
| agents/workflow-improver.md:146–166 | Reads resolution.*, category from archived state.yaml |
