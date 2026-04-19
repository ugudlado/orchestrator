# Design: Feature Complexity Tracking

## Context

`orchestrator cost --repo` today reports per-step, per-agent, per-tool, and per-feature
cost aggregates using a single DuckDB table `step_events` keyed on
`(repo_root, change_id, step_id)`. There is no feature-level metadata dimension:
we know how much each feature cost, but not what class of feature it was.

Complexity as a concept already exists in the system — `design-and-draft-artifacts.yaml`
uses an XS/S/M/L label set internally to pick between approaches — but the label is
never written to disk or persisted to DuckDB. Promoting it to a first-class dimension
requires three pieces: (1) writing it into state.yaml, (2) persisting it into DuckDB
at change-completion time, (3) extending the report CLI to aggregate by it.

Constraints the discovery surfaced:

- A table named `features` already exists in `metrics.duckdb`, created by
  `register-repo.sh` (bash-owned) with a schema focused on batch-ingest payloads.
  Adding a Python-written column to that table creates two writers on the same table
  and couples real-time reporting to a separate rebuild workflow.
- 21 archived state.yaml files exist without a `complexity` field. Backfill is
  explicitly out of scope for this change.
- `design-and-draft-artifacts.yaml` uses `{XS, S, M, L}` today — the closed set must
  be extended to include `XL` for parity with the state.yaml contract.

## Goals / Non-Goals

### Goals

- Persist a per-feature `complexity` label into DuckDB at change-completion time.
- Allow `orchestrator cost --repo --by complexity` to group cost aggregates by bucket.
- Remain non-breaking for the 21 archived states and for any state.yaml without a
  `complexity:` field.
- Keep the Python write path self-contained — no coupling to `register-repo.sh`.

### Non-Goals

- Backfilling the 21 archived features with a complexity label.
- Modifying the bash-owned `features` table or `register-repo.sh`.
- Enforcing `complexity:` as a required state.yaml field or a required step output.
- Computing trend lines or historical buckets (e.g., "average M cost over last
  quarter"). The report is a point-in-time aggregate.
- UI changes — CLI stdout only.

## Approaches Considered

### Approach A: Add `complexity` column to bash-owned `features` table

Use `ALTER TABLE features ADD COLUMN IF NOT EXISTS complexity VARCHAR`. `register-repo.sh`
reads `complexity:` from each state.yaml during ingest; reporting JOINs against this
column.

- Pros: single canonical features table, leverages existing `--rebuild` backfill.
- Cons: couples on-demand report to a batch-rebuild run, two writers on the same
  table once the Python path also needs to write. Report accuracy depends on when
  `register-repo.sh --rebuild` last ran.

### Approach B: New Python-owned table `feature_complexity`

Create a new table `feature_complexity` with columns `(repo_root, change_id,
complexity, schema_name, started_at, completed_at, upserted_at)`. `upsert.py` owns
the DDL and the upsert function; `mark-change-completed.sh` triggers a single upsert
per change. Reporting JOINs `step_events` with `feature_complexity`.

- Pros: no table-name collision, Python owns both write and read paths, real-time
  write at change-completion, consistent with how `step_events` and `tool_calls` are
  managed, `register-repo.sh` is untouched.
- Cons: two tables (`features` bash-owned, `feature_complexity` Python-owned) with
  overlapping identifier columns — conceptual near-duplication.

### Approach C: Dual-writer on the bash-owned `features` table

Both `register-repo.sh` and the Python path write to `features`, coordinating on the
`complexity` column only.

- Pros: single canonical table.
- Cons: dual-writer coordination, shared DDL ownership between bash and Python,
  harder to reason about ordering, more code to maintain.

### Selected Approach

**Approach B — Python-owned `feature_complexity` table.** Two reasons dominate:
(1) it is the simplest approach that fully resolves the name collision and keeps the
write-path story local to one language, and (2) it preserves the existing
`register-repo.sh` pipeline as-is, avoiding dual-writer complexity. The conceptual
duplication with the bash `features` table is acceptable because the two tables
serve different consumers — `features` is a batch-ingest payload table,
`feature_complexity` is a real-time metadata dimension for the cost report.

## High-Level Design

### Architecture Overview

```
 architect step               mark-change-completed.sh                orchestrator cost
      │                                │                                     │
      ▼                                ▼                                     ▼
 state.yaml                    upsert_feature_complexity ─────►  aggregate_repo(scope=
 + complexity: M                        │                         "complexity")
                                        ▼                                     │
                             metrics.duckdb                                   │
                             ├── step_events (existing) ◄────────────── JOIN ─┘
                             ├── tool_calls  (existing)                       │
                             ├── features    (bash, untouched)                │
                             └── feature_complexity (NEW) ◄──────────────────┘
```

### Key Abstractions

- **`State.complexity: str | None`** — the authoritative in-memory representation of
  the field. Set by `load_state()` after validation.
- **`feature_complexity` table** — the persistent metadata dimension. One row per
  completed feature, keyed by `(repo_root, change_id)`.
- **`aggregate_repo(scope="complexity")`** — the new dispatch arm. Returns a
  bucket-ordered list of rows with counts and cost statistics.

## Low-Level Design

### Components

**1. `parser.py` — State dataclass extension**

```python
@dataclass
class State:
    # ... existing fields ...
    complexity: str | None = None

_COMPLEXITY_VALUES = frozenset({"XS", "S", "M", "L", "XL"})

def load_state(path: Path) -> State:
    raw = yaml.safe_load(path.read_text()) or {}
    complexity = raw.get("complexity")
    if complexity is not None and complexity not in _COMPLEXITY_VALUES:
        sys.stderr.write(
            f"[complexity] ignoring unknown value {complexity!r} "
            f"for {raw.get('change_id', '<unknown>')}\n"
        )
        complexity = None
    return State(..., complexity=complexity, raw=raw)
```

**2. `upsert.py` — DDL and upsert**

```python
_DDL_FEATURE_COMPLEXITY = """
CREATE TABLE IF NOT EXISTS feature_complexity (
  repo_root    VARCHAR NOT NULL,
  change_id    VARCHAR NOT NULL,
  complexity   VARCHAR,
  schema_name  VARCHAR,
  started_at   TIMESTAMP,
  completed_at TIMESTAMP,
  upserted_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (repo_root, change_id)
)
"""

def ensure_schema(con):
    con.execute(_DDL_STEP_EVENTS)
    con.execute(_DDL_TOOL_CALLS)
    con.execute(_DDL_FEATURE_COMPLEXITY)  # NEW

def upsert_feature_complexity(con, repo_root, change_id, complexity,
                               schema_name, started_at, completed_at):
    con.execute(
        "INSERT OR REPLACE INTO feature_complexity "
        "(repo_root, change_id, complexity, schema_name, started_at, completed_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [repo_root, change_id, complexity, schema_name, started_at, completed_at],
    )
```

`complexity` is NULL-able. A call with `complexity=None` still writes the row — this
records the feature's existence for completeness even when no label was set.

**3. `cost_report.py` — complexity arm**

```python
_BUCKET_ORDER = ["XS", "S", "M", "L", "XL", "unknown"]

def _by_complexity(con, repo_root):
    rows = con.execute("""
        WITH feature_cost AS (
          SELECT change_id, SUM(gen_ai_usage_cost_usd) AS cost
          FROM step_events
          WHERE repo_root = ?
          GROUP BY change_id
        )
        SELECT
          COALESCE(fc.complexity, 'unknown') AS bucket,
          COUNT(DISTINCT fcost.change_id)    AS features,
          SUM(fcost.cost)                    AS total_cost,
          MEDIAN(fcost.cost)                 AS median_cost,
          QUANTILE_CONT(fcost.cost, 0.9)     AS p90_cost
        FROM feature_cost fcost
        LEFT JOIN feature_complexity fc
          ON fc.repo_root = ? AND fc.change_id = fcost.change_id
        GROUP BY bucket
    """, [repo_root, repo_root]).fetchall()

    ordered = sorted(rows, key=lambda r: _BUCKET_ORDER.index(r[0]))
    return ordered
```

Dispatch in `aggregate_repo()`:

```python
if scope == "complexity":
    return _by_complexity(con, repo_root)
```

Render appends a new markdown table when `by == "complexity"`, using the same
`render_markdown_repo()` frame as existing arms.

**4. `mark-change-completed.sh` — upsert trigger**

After the existing state.yaml writes, a short Python invocation (using the shebang
pattern already used elsewhere in the file) calls `upsert_feature_complexity` with
values read from state.yaml. Wrapped in `|| true` so DB contention never blocks
completion.

**5. `bin/orchestrator` — argparse**

```python
parser.add_argument(
    "--by",
    choices=["step", "agent", "tool", "feature", "complexity"],
    default=None,
)
```

**6. `config/steps/design-and-draft-artifacts.yaml`**

- Extend complexity label set documentation to `{XS, S, M, L, XL}`.
- Add `complexity` to the step's declared state.yaml outputs (documentation; not
  enforced by `orchestrator record`).

### Data Flow

1. Architect writes `complexity: M` into state.yaml during
   `design-and-draft-artifacts`.
2. Workflow runs to `mark-change-completed`.
3. `mark-change-completed.sh` reads state.yaml, calls `upsert_feature_complexity()`
   with `(repo_root, change_id, 'M', schema, started_at, completed_at)`.
4. DuckDB stores the row keyed on `(repo_root, change_id)`.
5. Operator runs `orchestrator cost --repo --by complexity`.
6. `aggregate_repo(scope="complexity")` computes per-change cost sums from
   `step_events`, LEFT JOINs against `feature_complexity`, groups by
   `COALESCE(complexity, 'unknown')`, orders buckets XS→S→M→L→XL→unknown, and
   prints a markdown table.

### State Management

- `state.yaml` gains an optional top-level `complexity:` field. No migration required
  — absence is semantically identical to pre-existing state.
- `metrics.duckdb` gains a `feature_complexity` table. `ensure_schema()` uses
  `CREATE TABLE IF NOT EXISTS` so it is idempotent across versions.
- No in-memory state beyond the `State` dataclass field.

### Error Handling

- **Unknown complexity value in state.yaml**: `load_state()` coerces to `None`, emits
  a single stderr warning line; dispatch continues. Never raises.
- **DuckDB unavailable / locked during upsert**: `mark-change-completed.sh` wraps
  the upsert call with `|| true`, matching the existing `upsert_step_event` error
  tolerance pattern in `bin/orchestrator:237-259`. Missing rows surface later in the
  report as `unknown` bucket contributors.
- **`feature_complexity` table absent on first run**: `ensure_schema()` creates it
  idempotently on every `orchestrator next` and on first report run; no special
  handling needed.
- **Features in `step_events` without a `feature_complexity` row**: the LEFT JOIN
  produces NULL complexity, which `COALESCE` maps to `unknown`.
- **Empty dataset**: `aggregate_repo(scope="complexity")` returns an empty list; the
  renderer prints the table header with no data rows.

## Constraints

- DuckDB v1.5.2 is the target; `MEDIAN()` and `QUANTILE_CONT()` are available.
- No new Python dependencies. All code paths use the stdlib, PyYAML (already present),
  and the DuckDB driver already used by `upsert.py`.
- `register-repo.sh` and the bash-owned `features` table must remain untouched.

## Trade-offs

- **Two tables named `features` and `feature_complexity`** — conceptual near-duplication
  in exchange for zero dual-writer complexity and zero collision risk. Acceptable
  because the two serve distinct consumers (batch ingest vs. real-time metadata).
- **No backfill of archived states** — 21 archives will show as `unknown` until
  someone annotates them. Acceptable because complexity is primarily a
  going-forward signal, and the `unknown` bucket makes the gap visible rather than
  hiding it.
- **Warn-don't-error on invalid values** — silently coercing typos to `None` may hide
  bugs. Acceptable because the alternative (breaking dispatch on a typo in an
  optional field) is strictly worse.

## Decisions

- **`feature_complexity` as table name** → avoids bash-owned `features` collision →
  two tables must be mentally separated by future readers, but the names are
  distinct enough to disambiguate.
- **Upsert at `mark-change-completed`** → single write per feature at the moment
  state.yaml is final → the `orchestrator next` main loop stays simple; no per-step
  redundant writes.
- **Declaration point = design phase** → the architect already picks a complexity
  label internally for approach-selection; persisting the same label is cheap and
  authoritative → workflow-init size estimates remain aspirational context, not
  source-of-truth.
- **`unknown` bucket for NULLs** → operators see untagged spend rather than it
  silently disappearing → makes backfill gaps visible.
- **Validation warns, not errors** → existing archives and typos don't break
  dispatch → a typo may silently become `unknown`; acceptable because a stderr
  warning is emitted and the field is not required.
- **`complexity` documented but not contract-enforced in
  `design-and-draft-artifacts.yaml` outputs** → older archives remain valid; new
  runs write the field but `orchestrator record` won't reject its absence.

## Open Questions

None. All six discovery open questions were resolved in this design (see the input
brief — OQ-1 through OQ-6 are all decided above).

<!-- Format contract: contracts/artifact-formats.md § Design Format Contract -->
