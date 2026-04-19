---
feature-id: feature-complexity-tracking
linear-ticket: HL-291
---

# Specification: Feature Complexity Tracking

## Motivation

Today, per-feature AI spend lives in `step_events` keyed by `change_id`, but there is
no way to roll costs up by feature size. The team cannot answer the simple question
"how much does an XL feature cost compared to an XS feature?" without hand-inspecting
individual change reports. Complexity is implicit in the design-phase approach-selection
heuristic (XS/S/M/L labels in `design-and-draft-artifacts.yaml`) but never persisted as
a first-class, queryable dimension.

This change promotes complexity to a top-level state.yaml field, persists it into a
DuckDB table, and extends `orchestrator cost --repo` with a `--by complexity` arm so
cost-per-bucket reporting becomes a one-command operation.

## What Changes

- `state.yaml` gains an optional top-level `complexity:` field (closed set
  `{XS, S, M, L, XL}` or absent).
- The architect's `design-and-draft-artifacts` step is the authoritative writer — it
  records the complexity label it already picks for approach-selection into state.yaml.
- The `State` dataclass in `orchestrator_next/parser.py` gains a
  `complexity: str | None = None` field; `load_state()` validates the value against the
  closed set and emits a stderr warning (not an error) when an unknown value appears,
  so existing 21 archived states continue to load unchanged.
- A new DuckDB table `feature_complexity` is created by `ensure_schema()` in
  `orchestrator_next/upsert.py`, keyed on `(repo_root, change_id)`. The name avoids
  collision with the existing bash-owned `features` table from `register-repo.sh`.
- `mark-change-completed.sh` calls `upsert_feature_complexity()` at change-completion
  time, when state.yaml has its final authoritative values. No new inline step is added.
- `cost_report.py` gains a `_by_complexity()` aggregate arm that JOINs `step_events`
  against `feature_complexity` and groups by bucket, with a synthetic `unknown` bucket
  for features that have no `feature_complexity` row or a NULL complexity.
- `bin/orchestrator` argparse adds `complexity` to the `--by` choices.
- `config/steps/design-and-draft-artifacts.yaml` extends its complexity label closed
  set to include `XL` and documents `complexity` as a state.yaml output the step writes.

## Requirements

### Functional

1. **FR-1**: The `State` dataclass MUST expose a `complexity: str | None` field
   populated from `state.yaml`'s top-level `complexity:` key via `load_state()`.
2. **FR-2**: `load_state()` MUST validate `complexity` against the closed set
   `{XS, S, M, L, XL}`. Unknown values MUST be coerced to `None` and emit a single
   stderr warning line (`[complexity] ignoring unknown value '<value>' for <change_id>`);
   loading MUST NOT raise.
3. **FR-3**: `ensure_schema()` MUST create table `feature_complexity` with columns
   `(repo_root VARCHAR NOT NULL, change_id VARCHAR NOT NULL, complexity VARCHAR,
   schema_name VARCHAR, started_at TIMESTAMP, completed_at TIMESTAMP, upserted_at
   TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (repo_root, change_id))` idempotently.
4. **FR-4**: `upsert_feature_complexity(con, repo_root, change_id, complexity,
   schema_name, started_at, completed_at)` MUST `INSERT OR REPLACE` a row keyed on
   `(repo_root, change_id)`. Callers passing a `None` complexity MUST still write the
   row (the row records the feature's existence; complexity is NULL-able).
5. **FR-5**: `mark-change-completed.sh` MUST invoke `upsert_feature_complexity()` once
   per change completion, reading `complexity`, `schema`, `created_at`, and
   `completed_at` from state.yaml. Upsert failure (DuckDB locked, file missing) MUST
   NOT block change completion — errors are logged to stderr and swallowed, matching
   the existing `upsert_step_event` pattern in `bin/orchestrator`.
6. **FR-6**: `cost_report.aggregate_repo(..., scope="complexity")` MUST return one row
   per complexity bucket, computed by JOINing feature-level cost aggregates (SUM of
   `gen_ai_usage_cost_usd` per `change_id` from `step_events`) against
   `feature_complexity`, and grouping by `complexity`. Columns: `complexity`,
   `features` (distinct `change_id` count), `total_cost`, `median_cost`, `p90_cost`.
7. **FR-7**: Feature `change_id`s that appear in `step_events` but have no row in
   `feature_complexity`, or have a row with `complexity IS NULL`, MUST be bucketed as
   `unknown`.
8. **FR-8**: `render_markdown_repo()` (or the equivalent render path for `--by`
   arms) MUST print complexity rows ordered `XS, S, M, L, XL, unknown`, omitting
   buckets that have zero features.
9. **FR-9**: `bin/orchestrator` argparse for `orchestrator cost --by` MUST accept
   `complexity` alongside the existing `step|agent|tool|feature` choices.
10. **FR-10**: `design-and-draft-artifacts.yaml` MUST list `complexity` among its
    state.yaml outputs and its complexity label set MUST be `{XS, S, M, L, XL}`.

### Non-Functional

1. **NFR-1**: No breaking change to existing state.yaml files. The 21 archived states
   without a `complexity` field MUST continue to load and dispatch unchanged.
2. **NFR-2**: No change to the bash-owned `features` table in `register-repo.sh`. The
   new `feature_complexity` table MUST be independently owned by the Python upsert path.
3. **NFR-3**: `--by complexity` MUST complete within the same order-of-magnitude
   latency as existing `--by feature` on the same dataset (one extra JOIN on a
   primary-key-indexed table).
4. **NFR-4**: Test coverage for new code paths MUST be ≥ 90%.

## Architecture

| File | Change |
|------|--------|
| `config/scripts/orchestrator_next/parser.py` | Add `complexity: str \| None = None` to `State` dataclass; validate in `load_state()` against `{XS,S,M,L,XL}`. |
| `config/scripts/orchestrator_next/upsert.py` | Add `_DDL_FEATURE_COMPLEXITY` constant; extend `ensure_schema()`; add `upsert_feature_complexity()`. |
| `config/scripts/orchestrator_next/cost_report.py` | Add `_by_complexity()`; wire `scope="complexity"` into `aggregate_repo()` dispatch; extend markdown render with ordered-bucket output and `unknown` bucket. |
| `scripts/inline/mark-change-completed.sh` | After writing final state.yaml, invoke `upsert_feature_complexity()` via a short Python one-liner, guarded with `|| true` to match existing error-tolerant patterns. |
| `bin/orchestrator` | Add `complexity` to `--by` argparse `choices`. |
| `config/steps/design-and-draft-artifacts.yaml` | Add `complexity` to the step's declared `outputs`; extend complexity label set to include `XL`. |

**Data flow**: architect step writes `complexity: <XS|S|M|L|XL>` to state.yaml →
`mark-change-completed.sh` reads state.yaml and calls `upsert_feature_complexity()` →
`feature_complexity` row persists in `metrics.duckdb` → `orchestrator cost --repo --by
complexity` JOINs `step_events` (per-change cost sum) with `feature_complexity`
(complexity bucket) → markdown table printed to stdout.

## Test Strategy

### Test File Paths

- `config/scripts/orchestrator_next/parser.py` → `config/scripts/orchestrator_next/tests/test_parser.py`
- `config/scripts/orchestrator_next/upsert.py` → `config/scripts/orchestrator_next/tests/test_upsert.py`
- `config/scripts/orchestrator_next/cost_report.py` → `config/scripts/orchestrator_next/tests/test_cost_report.py`

Follow the existing test-file layout under `config/scripts/orchestrator_next/tests/`.

### Coverage Targets

≥ 90% line coverage for all three modified modules. No regression in existing
coverage.

### Key Test Scenarios

1. `State.complexity` populated when state.yaml has `complexity: M`; `None` when
   absent.
2. `load_state()` emits stderr warning and coerces to `None` for `complexity: XXXX`;
   no exception.
3. `ensure_schema()` creates `feature_complexity` on a fresh DB; second call is a
   no-op.
4. `upsert_feature_complexity()` round-trips a row; re-upsert replaces; NULL
   complexity row persists.
5. `aggregate_repo(scope="complexity")` returns ordered buckets with correct counts,
   totals, medians, and p90s across a seeded dataset covering all five buckets plus
   unknown.
6. Features present in `step_events` without a `feature_complexity` row fall into the
   `unknown` bucket.
7. `orchestrator cost --repo --by complexity` CLI smoke: argparse accepts the flag;
   stdout includes the five-column markdown table.

## Acceptance Criteria

- AC-1: Given a workflow in design phase, when the architect step writes
  `complexity: M` to state.yaml and the workflow reaches `mark-change-completed`,
  then a row `(repo_root, change_id, 'M', ...)` exists in `feature_complexity`.
  [traces: UC-1]
- AC-2: Given `feature_complexity` and `step_events` both contain rows across XS/S/M/L/XL,
  when `orchestrator cost --repo --by complexity` runs, then stdout prints a markdown
  table with one row per populated bucket ordered XS → S → M → L → XL → unknown, with
  columns `complexity | features | total_cost | median_cost | p90_cost`.
  [traces: UC-2]
- AC-3: Given a feature whose state.yaml has no `complexity:` field, when the feature
  is completed and `--by complexity` runs, then its cost contribution appears in the
  `unknown` bucket and no error is raised.
  [traces: UC-3, UC-E3]
- AC-4: Given state.yaml contains `complexity: XXXX`, when `load_state()` runs, then
  `State.complexity` is `None`, a single stderr warning is emitted, and dispatch
  continues.
  [traces: UC-E1]
- AC-5: Given a pre-existing `metrics.duckdb` created before this feature landed,
  when `orchestrator next` next runs, then `ensure_schema()` creates the
  `feature_complexity` table idempotently and no error is raised.
  [traces: UC-E2]
- AC-6: Given `register-repo.sh` has previously created the `features` table, when
  this feature's code executes, then the bash-owned `features` table schema is
  unchanged.
  [traces: UC-2, NFR-2]

## Alternatives Considered

**Alternative A: Add `complexity` column to the existing bash `features` table**
Rejected. `features` is owned by `register-repo.sh` (a batch-ingest tool) and uses a
different upsert rhythm than the Python real-time path. Mutating its schema from
Python creates two writers on the same column and couples an on-demand reporting
feature to a separate batch-rebuild workflow.

**Alternative C: Dual-writer on the existing `features` table**
Rejected. Same dual-writer problem as Alternative A plus additional coordination
complexity — both writers must agree on column semantics and ordering.

**Selected: Approach B — new Python-owned `feature_complexity` table.** It sidesteps
the name collision, keeps the Python write path self-contained (consistent with how
`step_events` and `tool_calls` are managed today), and leaves `register-repo.sh`
entirely untouched.

**Alternative: Declare complexity at workflow-init from idea.md size estimate**
Rejected. Idea-level size estimates are aspirational; the architect's design-phase
approach selection is the first point where a complexity label is grounded in
concrete design work. Writing at design time gives a more reliable signal.

**Alternative: Upsert on every `orchestrator next` call in the main loop**
Rejected. `mark-change-completed` is the natural single upsert point — it runs once,
after the final state is authoritative. Upserting on every `next` call writes the
same row repeatedly for no benefit and couples the reporting table to step-event
dispatch.

## Impact

**No breaking changes.** Existing state.yaml files without a `complexity:` field
continue to load and dispatch. The new `feature_complexity` table is additive. The
existing bash-owned `features` table is untouched. The 21 archived states will show
up in `--by complexity` as the `unknown` bucket until manually annotated (explicitly
out of scope for this change).

**Affected areas**: `orchestrator_next` Python package, `orchestrator cost` CLI,
`mark-change-completed.sh` inline step, `design-and-draft-artifacts.yaml` step
contract.

## Decisions

- **Table name `feature_complexity`**: avoids collision with the bash-owned `features`
  table in `register-repo.sh`; names the dimension the table persists.
- **Declaration point: design phase**: the architect step already picks XS/S/M/L/XL
  internally for approach selection — recording that same value to state.yaml is
  cheap and authoritative.
- **Upsert at mark-change-completed**: a single write per feature at the point where
  state.yaml is final; no per-step redundant writes.
- **Validation warns, does not error**: the closed set is enforced at load time but
  invalid values are coerced to `None` so existing archives and typos never break
  dispatch.
- **Unknown bucket in the report**: more informative than silently dropping NULL
  rows; lets operators see how much spend is untagged.
- **`complexity` optional in step contract**: listed as an output of
  `design-and-draft-artifacts` for documentation, but not enforced by `orchestrator
  record`, so older archives remain valid.

<!-- Format contract: contracts/artifact-formats.md § Specification Format Contract -->
