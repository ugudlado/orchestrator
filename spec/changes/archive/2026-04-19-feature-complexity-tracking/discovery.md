---
feature-id: feature-complexity-tracking
linear-ticket: HL-291
---

# Discovery Brief: Feature Complexity Tracking (HL-291)

## What I Understand

The underlying goal is to make feature complexity a first-class queryable dimension in cost reporting — so the team can answer "how much do XL features cost vs XS features on average?" rather than inspecting individual change reports.

The stated mechanism: add a `complexity:` field (closed set: XS/S/M/L/XL) to workflow state.yaml, create a DuckDB `features` table with complexity and metadata, and extend `orchestrator cost --repo` with a `--by complexity` flag.

## What Already Exists

### Codebase

**`config/scripts/register-repo.sh:77-87`** — a `features` table already exists in `metrics.duckdb` with schema `(repo_root, change_id, schema, status, started_at, completed_at, payload_json, ingested_at)`. The upsert pattern is `INSERT OR REPLACE INTO features` keyed on `(repo_root, change_id)`. This table was introduced by the cross-repo-metrics-duckdb feature and has been operational since 2026-04-17.

**This is a critical collision**: the task description says "create a new DuckDB `features` table" but the table name `features` is already taken. `ensure_schema()` in `upsert.py` uses `CREATE TABLE IF NOT EXISTS` — if HL-291 adds a `features` DDL to `upsert.py` with different columns, the second CREATE silently no-ops because the table already exists with the wrong schema. The collision must be resolved before implementation begins.

**`config/scripts/orchestrator_next/upsert.py:27-71`** — `ensure_schema()` creates only `step_events` and `tool_calls`. There is no `features` or feature-metadata table in the Python upsert path today.

**`config/scripts/orchestrator_next/cost_report.py:399-494`** — `aggregate_repo()` handles `--repo` scope. It already dispatches on `scope` (None/`feature`, `agent`, `tool`). Adding `complexity` scope requires a `JOIN` between `step_events` and a features-metadata table — the existing `scope` arms are single-table reads.

**`bin/orchestrator:59-62`** — `_cost_main()` argparse. The `--by` flag currently accepts `step|agent|tool|feature`. Adding `complexity` requires extending `choices`.

**`config/scripts/orchestrator_next/parser.py:55-64`** — `State` dataclass has no `complexity` field. `load_state()` returns `State` which includes `raw` (the full YAML) — any caller can read `raw.get("complexity")` without a dataclass change, but that is fragile. A clean add is a `complexity: str | None = None` field on `State`.

**`config/steps/design-and-draft-artifacts.yaml:42-48`** — the architect step already uses `XS/S/M/L` as approach-complexity labels in its auto-selection heuristic. HL-291 adds `XL` to the set and promotes this to a feature-level field. The design-and-draft-artifacts contract does NOT currently include `complexity` in its `outputs:` list.

**`config/steps/mark-change-completed.yaml`** — inline step, currently writes `status`, `completed_at`, `archive_path` to state.yaml. A natural insertion point for a `features` upsert if complexity is already populated in state.yaml at that point.

**`bin/orchestrator:237-259`** — the `orchestrator next` main loop already runs `upsert_step_event` for every terminal step event, inside a `try/except` that skips silently on DB errors. The features upsert could be added here alongside `upsert_step_event` calls, or in a separate path.

**`spec/changes/archive/*/state.yaml`** (21 directories) — `grep -rn "^complexity:" spec/changes/archive/*/state.yaml` returns zero results. No archived state has a top-level `complexity` field. All 21 archives need backfill.

**`spec/project.yaml:62-67`** — project learning: "Use a single wide features table keyed on (repo_root, change_id) with explicit typed columns and a payload_json VARCHAR for the full state.yaml content." This learning is about `register-repo.sh`'s bash-ingest path but applies equally to the Python path.

### External

No external libraries needed. DuckDB's `APPROX_QUANTILE` or `PERCENTILE_CONT` window function handles median/p90 directly. DuckDB v1.5.2 is installed (`config/scripts/orchestrator_next/upsert.py` test harness confirmed this).

## Critical Finding: Table Name Collision

`features` is already taken in `metrics.duckdb` by `register-repo.sh`. The approaches below each address this differently. An ADD COLUMN approach, a rename approach, and a separate Python-owned table approach are all viable — but the architect must pick one before implementation.

## Build or Reuse?

Extend existing code, not build new. The `--by complexity` reporting path extends `aggregate_repo()` and the `--by` argparse choices. The complexity field on state.yaml is a simple new field. The only genuinely new artifact is the feature-metadata persistence layer — and whether that means extending the existing `features` table (bash-owned) or creating a new Python-owned table is the core decision.

## Approaches Considered

### Approach A: Add `complexity` column to existing bash `features` table + JOIN in cost_report

Extend `register-repo.sh` to `ALTER TABLE features ADD COLUMN IF NOT EXISTS complexity VARCHAR`. `register-repo.sh` reads `complexity` from each state.yaml during ingest and populates the column. `cost_report.py`'s `--by complexity` JOIN reads from this table.

- Build or reuse: reuse the existing `features` table
- Pros: single canonical features table, no name conflicts, leverages existing re-ingest (`--rebuild`) for backfill, no new Python DDL, `spec/project.yaml` learning upheld
- Cons: two separate systems own data for the same logical entity (bash ingest writes complexity; Python writes step_events); `orchestrator cost --by complexity` requires the bash `register-repo.sh` to have been run recently to be accurate; cross-system coupling
- Effort: small

### Approach B: New Python-owned table with distinct name (e.g., `feature_complexity`)

Add `_DDL_FEATURE_COMPLEXITY` to `upsert.py` creating a table named `feature_complexity` (or `feature_metadata`) with `(repo_root, change_id, complexity, started_at, completed_at, schema)`. Upsert from `orchestrator next` when `state.raw.get("complexity")` is non-null, or from `mark-change-completed` inline step. `--by complexity` JOINs `step_events` with `feature_complexity`.

- Build or reuse: build new (new table name avoids collision, stays in Python)
- Pros: no collision with bash-owned `features`, Python owns both write paths, complexity is captured in real-time without a manual `register-repo.sh` run, consistent with how `step_events` + `tool_calls` are managed
- Cons: two tables named `features` (bash) and `feature_complexity` (Python) for similar purposes — conceptual duplication; `--by complexity` JOIN must handle NULL complexity rows
- Effort: small-to-medium

### Approach C: Extend existing bash `features` table schema AND populate from Python on `orchestrator next`

`register-repo.sh` already owns the `features` DDL. Python adds `ALTER TABLE features ADD COLUMN IF NOT EXISTS complexity VARCHAR` in `ensure_schema()`. Python upserts/updates the complexity column whenever `orchestrator next` processes a state with `complexity` set. `register-repo.sh` also reads and writes the column on rebuild.

- Build or reuse: extend the existing `features` table from both write paths
- Pros: one canonical features table, real-time Python writes, full backfill via bash `--rebuild`
- Cons: two writers on the same table and column; ordering/race not guaranteed in theory (low practical risk since scripts run sequentially); `ensure_schema()` must issue DDL-altering statements which are safe with `IF NOT EXISTS` / `IF NOT EXISTS`; more complex coordination
- Effort: medium

## Recommendation

Approach B. It avoids the table-name collision entirely, keeps the Python write path self-contained (consistent with how `step_events` and `tool_calls` are managed), and doesn't require modifying bash scripts that are owned by a separate ingest pipeline. The table should be named `feature_complexity` (not `features`) to be unambiguous.

The tradeoff: two tables for overlapping metadata. This is the correct tradeoff given that `register-repo.sh` is a batch-ingest tool while `orchestrator next` is a real-time event emitter — they serve different consumers.

If the architect prefers a single unified `features` table, Approach C is the fallback. Approach A is viable but requires that `register-repo.sh --rebuild` be run before `--by complexity` reports are accurate, which is a UX gotcha.

## Personas

- **P1: Workflow operator** — runs `orchestrator cost --repo --by complexity` to understand which complexity bucket is consuming the most AI spend.
- **P2: Workflow initiator** — declares `complexity: M` (or similar) when starting a workflow so the field is available for reporting.
- **P3: Architect** — assigns complexity during design phase; wants the field visible in the final cost report.

## Use Cases

### Happy Path

UC-1: Declare complexity at workflow init — a workflow initiator runs `/orchestrate` on a new feature idea; the workflow-init step writes `complexity: M` to state.yaml; `orchestrator next` picks it up and upserts a row into `feature_complexity`; later `orchestrator cost --repo --by complexity` shows the M bucket with this feature's cost included.

UC-2: Complexity-bucketed cost summary — an operator runs `orchestrator cost --repo --by complexity`; the command JOINs `step_events` with `feature_complexity`, groups by complexity bucket, and prints a table with columns `complexity | count | median_cost | p90_cost | total_cost`; rows are ordered XS → S → M → L → XL → unknown.

UC-3: Feature without complexity — a feature has no `complexity` field in state.yaml; the `feature_complexity` table has no row for it (or a row with NULL complexity); `--by complexity` places it in an `unknown` bucket rather than failing.

### Error and Edge Cases

UC-E1: Invalid complexity value — state.yaml has `complexity: XXXX`; `upsert.py` validates against the closed set `{XS, S, M, L, XL}` and either rejects with a warning or normalizes to NULL; the complexity upsert is skipped; dispatch is not blocked.

UC-E2: `feature_complexity` table absent — `metrics.duckdb` exists but was created before this feature; `ensure_schema()` runs `CREATE TABLE IF NOT EXISTS feature_complexity` idempotently on next `orchestrator next` invocation; no error.

UC-E3: `--by complexity` with no feature_complexity rows — no features have a complexity field yet; `aggregate_repo(..., scope="complexity")` returns an `unknown` bucket with all costs, or an empty rows list if we require an explicit complexity match; both behaviors must be documented.

## Scope

### In-Scope

- `complexity:` top-level field in state.yaml (closed set: XS/S/M/L/XL; null if absent)
- `State` dataclass in `parser.py` extended with `complexity: str | None = None`
- `_DDL_FEATURE_COMPLEXITY` DDL and `upsert_feature_complexity()` function in `upsert.py`
- `ensure_schema()` extended to create `feature_complexity` table
- `aggregate_repo()` extended with `scope="complexity"` path in `cost_report.py`
- `render_markdown_repo()` extended with complexity table render
- `_cost_main()` argparse extended with `complexity` in `--by` choices
- Complexity validation: `{XS, S, M, L, XL}` or None; invalid values → None with stderr warning
- Ordered bucket display: XS, S, M, L, XL, unknown
- `feature_complexity` upsert called in `orchestrator next` main loop (alongside existing step_events upsert), conditional on `state.raw.get("complexity")` being set

### Out-of-Scope

- Modifying `register-repo.sh` or the bash-owned `features` table
- Adding `complexity` to `design-and-draft-artifacts.yaml` outputs contract (the step can write it to state.yaml directly without being declared as a formal output — or this is a follow-up)
- Backfilling existing 21 archived features with complexity labels (manual annotation task, not this ticket)
- Median/p90 using per-row step cost (only available in `step_events`, where `gen_ai_usage_cost_usd` per row is a step cost, not a feature cost); feature-level cost must be aggregated per change_id from `step_events` then JOINed with complexity
- UI changes

## UI Direction

N/A — CLI stdout output only.

## Key Decisions

Open for architect. Core decision: resolve the `features` table name collision (Approach A/B/C). Secondary decisions: complexity declaration point (workflow-init vs design phase), upsert trigger (orchestrator next loop vs mark-change-completed inline), backfill strategy.

## Technical Context

| File | Role |
|------|------|
| `config/scripts/orchestrator_next/upsert.py` | Add `_DDL_FEATURE_COMPLEXITY`, `upsert_feature_complexity()`, extend `ensure_schema()`. |
| `config/scripts/orchestrator_next/cost_report.py` | Add `_by_complexity()` function, extend `aggregate_repo()` with `scope="complexity"`, extend `render_markdown_repo()`. |
| `bin/orchestrator:59-62` | Extend `--by` choices to include `complexity`. |
| `bin/orchestrator:237-259` | Call `upsert_feature_complexity()` here (same loop as `upsert_step_event`). |
| `config/scripts/orchestrator_next/parser.py:55-64` | Add `complexity: str | None = None` to `State` dataclass; read from `raw.get("complexity")` in `load_state()`. |
| `config/steps/design-and-draft-artifacts.yaml` | Existing `XS/S/M/L` step-approach labels; consider whether `complexity:` in state.yaml is set here or at workflow-init (open question). |
| `config/scripts/register-repo.sh:77-87` | Existing `features` DDL (bash-owned). Do NOT touch under Approach B. |

Key line numbers:
- `bin/orchestrator:59-62` — `--by` choices to extend
- `bin/orchestrator:237-259` — upsert loop to extend
- `config/scripts/orchestrator_next/upsert.py:156-165` — `ensure_schema()` to extend
- `config/scripts/orchestrator_next/cost_report.py:399-494` — `aggregate_repo()` to extend
- `config/scripts/orchestrator_next/parser.py:55-64` — `State` dataclass
- `config/scripts/register-repo.sh:77` — existing `features` table DDL (collision point)

Proposed `feature_complexity` DDL (Approach B):
```sql
CREATE TABLE IF NOT EXISTS feature_complexity (
  repo_root    VARCHAR NOT NULL,
  change_id    VARCHAR NOT NULL,
  complexity   VARCHAR,         -- XS/S/M/L/XL or NULL
  schema_name  VARCHAR,
  started_at   TIMESTAMP,
  completed_at TIMESTAMP,
  upserted_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (repo_root, change_id)
)
```

The `--by complexity` report shape:
```
| Complexity | Features | Total Cost | Median Cost | p90 Cost |
```
SQL requires: GROUP BY complexity from `feature_complexity`, JOIN with `step_events` for cost aggregation. Features with no `feature_complexity` row fall into `unknown` bucket.

## Open Questions

OQ-1: Table name — Should the new Python-owned table be `feature_complexity` (Approach B) or should the existing `features` table be extended via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS complexity` (Approach C)? This is the primary architectural decision for the architect.

OQ-2: Complexity declaration point — Is `complexity:` set at workflow-init time (from the backlog idea.md or the Linear ticket), at design phase (by the architect in `design-and-draft-artifacts`), or at both? The `design-and-draft-artifacts` contract already uses `XS/S/M/L` for approach selection — should the chosen approach's complexity become the feature's complexity? The closed set needs `XL` added.

OQ-3: Upsert trigger — Should `feature_complexity` be upserted on every `orchestrator next` call when complexity is set (like `step_events`), or only at `mark-change-completed` time, or both? The `orchestrator next` loop is the path of least resistance; `mark-change-completed` gives a confirmed-final value.

OQ-4: `--by complexity` with NULL/unknown rows — Should features with no complexity appear as an `unknown` bucket in the report, or be silently omitted? Recommend `unknown` bucket (more informative), but leave to architect.

OQ-5: Backfill — 21 archived features have no complexity. Should the architect: (a) leave NULL/unknown and live with sparse data until new features accumulate, (b) mandate manual annotation as a separate follow-up, or (c) add a one-time annotation script? Recommendation: option (a) with `unknown` bucket in the report — complexity is only meaningful going forward.

OQ-6: `design-and-draft-artifacts.yaml` contract — Should the `outputs:` list be extended with `complexity` so that `orchestrator record` validates its presence? Or should complexity remain an optional top-level state.yaml field written directly without contract enforcement?
