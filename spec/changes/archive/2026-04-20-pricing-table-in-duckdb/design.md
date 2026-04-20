# Design: Pricing table in DuckDB — Phase 1 of workflow-engine-as-state-machine

## Context

`config/pricing.yaml` is today read by three independent consumers: `record.py` (Python, per-step cost on the write path), `cost_report.py` (Python, display-metric via a re-export from `record.py`), and `estimate-cost.sh` (bash + AWK, pre-flight preview). There is no history of price changes, no atomic unit for audit, and no way to compute temporally-correct historical costs.

This feature moves the data into a DuckDB `pricing` table and lands a tiny migration runner (tracking table + ordered SQL files) so that phases 2–5 of the parent `workflow-engine-as-state-machine` effort inherit a working pattern. The runner is strictly additive: it sits alongside the existing inline `_migrate_step_events` / `_migrate_tool_calls` Python ALTER helpers in `upsert.py`, which stay as-is. Pricing is the first table created via the runner; every new table from phase 2 onward follows suit.

Existing system boundaries:

- `ensure_schema(db)` is the sole schema-evolution entry point and is already called from 5 sites (bin/orchestrator main dispatch, ingest-driver, ingest-subagents, mark-change-completed.sh, ingest-feature-metrics.py) plus `doctor.py` line 145-146 (RW) — all transparently pick up the runner.
- Two read-only openers (`_cost_main`, `_metrics_main`) skip `ensure_schema`; they must continue to work on a pricing-less DB.
- `step_events` live-schema (verified against `config/scripts/orchestrator_next/upsert.py` `_DDL_STEP_EVENTS`): columns include `model VARCHAR`, `cost_usd DOUBLE`, `started_at TIMESTAMP`, `ended_at TIMESTAMP`. No field-name drift in this design's SQL sketches.

## Goals / Non-Goals

### Goals

- A single `pricing` table as the source of truth, read by all three consumers.
- A reusable, idempotent migration runner backed by a `schema_migrations` tracking table.
- Zero new runtime dependencies; zero new CLI subcommands.
- Historical pricing retained (new rows, not in-place updates) via an `effective_from` column, even though phase 1 does not yet exploit it.
- `estimate-cost.sh` survives deletion of `config/pricing.yaml`.
- Graceful degradation when the read-only consumers open a DB that predates the pricing migration.

### Non-Goals

- Retroactively re-pricing historical `step_events.cost_usd` rows.
- Extending the runner to cover the existing `_migrate_*` inline Python ALTERs (risky double-ALTER).
- A query API, CLI verb, or REST surface on top of the pricing table.
- Migrating to `TIMESTAMPTZ` project-wide.
- Solving temporal `gross_usd` correctness in `cost_report.py` — explicitly deferred with an in-code TODO.

## Approaches Considered

### Approach A: Custom migration runner + `schema_migrations` table + standalone ingestion script (selected)

A 40-line `_run_migrations(db)` function added to `config/scripts/orchestrator_next/upsert.py` as a sibling to the existing `_migrate_step_events` / `_migrate_tool_calls` helpers — no new module. Creates `schema_migrations(name PK, applied_at)` if absent, lists `*.sql` files under `config/scripts/orchestrator_next/migrations/`, sorts lexically, skips any name already in the tracking table, executes each remaining file, records it. `ensure_schema()` calls this after the existing legacy ALTER helpers. Seed migration `0001_seed_pricing.sql` creates `pricing` DDL and INSERTs every model plus `__default__` plus local-model row. Recurring price updates happen via a new `scripts/ingest-pricing.py` that INSERTs a new row with a new `effective_from` — ingestion is NOT migration. `estimate-cost.sh` is rewritten to query via `duckdb -json -c "SELECT…"`.

Pros: zero new deps, follows existing patterns, SQL files are auditable diffs, tracking table enables future migration analytics, data changes decoupled from schema changes.
Cons: forward-only (acceptable for DDL); custom code to maintain.
Complexity: **M**.

### Approach B: Off-the-shelf migration library (yoyo-migrations)

Runtime dep + yoyo config. Does not solve the bash consumer. Rejected in discovery; not re-evaluated.

Complexity: M.

### Approach C: Re-seed from YAML on every startup (no runner)

Defeats the goal. Rejected in discovery; not re-evaluated.

Complexity: S (but wrong).

### Selected Approach

**Approach A.** It is the simplest build that meets all phase-1 goals, respects the two-verb end-state constraint of the parent effort, has no runtime-dependency cost, and produces a runner that phases 2–5 will reuse. B and C are ruled out by the "single source of truth" goal and the dependency-weight constraint respectively.

## High-Level Design

### Architecture Overview

```
┌──────────────┐         ┌────────────────────────┐
│ bin/orchestra│ ──┐     │ orchestrator_next/     │
│ tor (record) │   │     │   upsert.py            │
└──────────────┘   │     │   ├─ ensure_schema()   │
                   │     │   │   ├─ legacy ALTERs │
┌──────────────┐   ├──→  │   │   └─ _run_migr…   │
│ record.py    │   │     │   ├─ _run_migrations() │
│ _compute_    │   │     │   └─ (reads *.sql)     │
│   cost_usd   │ ──┤     └────────────────────────┘
└──────────────┘   │                 │
                   │                 ▼
┌──────────────┐   │     ┌────────────────────────┐
│ cost_report  │ ──┤     │ DuckDB                 │
│ .py (RO-ok)  │   │     │   schema_migrations    │
└──────────────┘   │     │   pricing              │
                   │     │   step_events          │
┌──────────────┐   │     │   tool_calls           │
│ estimate-    │ ──┘     │   …                    │
│ cost.sh      │ (via    └────────────────────────┘
└──────────────┘ duckdb             ▲
                 CLI)               │
┌──────────────┐                    │
│ ingest-      │ ───────────────────┘
│ pricing.py   │   (INSERT new effective_from row)
└──────────────┘
```

### Key Abstractions

- **Migration file**: a plain `.sql` file under `config/scripts/orchestrator_next/migrations/`. Name is its identity. DDL + seed data. No down-migration.
- **Migration runner**: `upsert._run_migrations(db)` — idempotent, transactional per file, invoked only from `ensure_schema()`. Sibling to the existing `_migrate_step_events` / `_migrate_tool_calls` helpers in the same module.
- **Pricing lookup**: a single parameterised SELECT with `effective_from <= ? ORDER BY effective_from DESC LIMIT 1`, with `__default__` as the sentinel fallback model_id.
- **Ingestion script**: `scripts/ingest-pricing.py` — no new CLI verb, just a runnable Python script. Data concern, not schema concern.

## Low-Level Design

### Components

#### 1. Migration runner in `config/scripts/orchestrator_next/upsert.py` (modify)

Add the following adjacent to `_migrate_step_events` / `_migrate_tool_calls`. No new module. One import site, sibling to the existing pattern.

```python
# Pseudocode — exact text in implementation task.
_DDL_SCHEMA_MIGRATIONS = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  name       VARCHAR PRIMARY KEY,
  applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

def _migrations_dir() -> Path:
    # Resolved from module __file__ — co-located with upsert.py.
    return Path(__file__).parent / "migrations"

def _run_migrations(db) -> list[str]:
    """Idempotent. Returns list of applied migration names (possibly empty)."""
    db.execute(_DDL_SCHEMA_MIGRATIONS)
    applied = {row[0] for row in db.execute(
        "SELECT name FROM schema_migrations"
    ).fetchall()}
    applied_now: list[str] = []
    for path in sorted(_migrations_dir().glob("*.sql")):
        if path.name in applied:
            continue
        sql = path.read_text()
        db.execute("BEGIN")
        try:
            db.execute(sql)
            db.execute("INSERT INTO schema_migrations(name) VALUES (?)", [path.name])
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
        applied_now.append(path.name)
    return applied_now
```

Inputs: open DuckDB RW connection.
Outputs: list of names applied this call; raises on SQL error (does NOT record a partial apply).
Dependencies: pathlib, duckdb connection contract.

#### 2. `config/scripts/orchestrator_next/migrations/0001_seed_pricing.sql` (new)

```sql
CREATE TABLE IF NOT EXISTS pricing (
  model_id            VARCHAR NOT NULL,
  input_usd           DOUBLE  NOT NULL,
  output_usd          DOUBLE  NOT NULL,
  cache_read_usd      DOUBLE  NOT NULL,
  cache_creation_usd  DOUBLE,
  is_local            BOOLEAN NOT NULL DEFAULT FALSE,
  effective_from      TIMESTAMP NOT NULL,
  PRIMARY KEY (model_id, effective_from)
);

INSERT OR REPLACE INTO pricing
  (model_id, input_usd, output_usd, cache_read_usd, cache_creation_usd,
   is_local, effective_from)
VALUES
  ('claude-opus-4-7',              15.00, 75.00, 1.50, 18.75, FALSE, '2025-01-01T00:00:00'),
  ('claude-opus-4-6',              15.00, 75.00, 1.50, 18.75, FALSE, '2025-01-01T00:00:00'),
  ('claude-opus-4-5',              15.00, 75.00, 1.50, 18.75, FALSE, '2025-01-01T00:00:00'),
  ('claude-sonnet-4-6',             3.00, 15.00, 0.30,  3.75, FALSE, '2025-01-01T00:00:00'),
  ('claude-sonnet-4-5',             3.00, 15.00, 0.30,  3.75, FALSE, '2025-01-01T00:00:00'),
  ('claude-haiku-4-5',              0.80,  4.00, 0.08,  1.00, FALSE, '2025-01-01T00:00:00'),
  ('claude-haiku-4-5-20251001',     0.80,  4.00, 0.08,  1.00, FALSE, '2025-01-01T00:00:00'),
  ('qwen/qwen3-coder-30b-a3b-instruct', 0.25, 1.00, 0.25, NULL, FALSE, '2025-01-01T00:00:00'),
  ('coder',                         0.00,  0.00, 0.00,  NULL, TRUE,  '2025-01-01T00:00:00'),
  ('__default__',                  15.00, 75.00, 1.50, 18.75, FALSE, '2025-01-01T00:00:00');
```

Notes: `PRIMARY KEY (model_id, effective_from)` allows multiple rows per model across time. `cache_creation_usd` is nullable because some models (qwen, coder) don't expose a separate cache-write rate; `_compute_cost_usd` already falls back to `input_usd` when null.

#### 3. `config/scripts/orchestrator_next/upsert.py` (modify `ensure_schema`)

```python
def ensure_schema(db) -> None:
    db.execute(_DDL_STEP_EVENTS)
    _migrate_step_events(db)
    db.execute(_CREATE_INDEX)
    db.execute(_DDL_TOOL_CALLS)
    _migrate_tool_calls(db)
    db.execute(_CREATE_TOOL_CALLS_INDEX)
    db.execute(_DDL_FEATURE_COMPLEXITY)
    db.execute(_DDL_FEATURE_METRICS)
    # New — runs after legacy ALTERs so order is deterministic.
    _run_migrations(db)
```

`_run_migrations` is module-local (defined in `upsert.py` itself) — no import needed and no circularity risk.

#### 4. `config/scripts/orchestrator_next/record.py` (modify `_compute_cost_usd`) — DB-acquisition strategy

New signature: `_compute_cost_usd(db, agent, usage, *, now=None) -> tuple[str|None, float|None]`. The function NEVER opens its own connection — it requires an open `db` from its caller.

**Caller inventory (verified by grep against HEAD)** — there are THREE call sites, not one:

| Site | File | Line | DB in scope? | Strategy |
|---|---|---|---|---|
| A | `config/scripts/orchestrator_next/record.py` | 394 | No — `record()` has no duckdb import | `record.main()` opens the connection, threads it through `record()` → `_compute_cost_usd`. |
| B | `bin/orchestrator` `_ingest_driver_main` | 337 | No — `db = duckdb.connect(...)` is at line 344, AFTER the call. | Move `_compute_cost_usd(...)` inside the `try:` block (after `ensure_schema(db)` at ~line 346) and pass `db`. Order becomes: connect → ensure_schema → compute → upsert. |
| C | `bin/orchestrator` `_ingest_subagents_main` | 473 | Yes — `db` already open at this point. | Prepend `db` to the call: `_compute_cost_usd(db, agent_name, usage)`. |

**Chosen strategy — Approach B' (DB acquired inside `record.main()`)**

`record.main()` is augmented to resolve a DuckDB path and open a short-lived RW connection:

```python
def main(argv: list[str]) -> int:
    # ... existing arg parsing, state load ...
    db = None
    try:
        db_path = os.environ.get("METRICS_DB") or (
            os.path.join(os.environ["ORCHESTRATOR_HOME"], "metrics.duckdb")
            if os.environ.get("ORCHESTRATOR_HOME") else None
        )
        if db_path:
            import duckdb
            from orchestrator_next.upsert import ensure_schema
            db = duckdb.connect(db_path)
            ensure_schema(db)  # idempotent; applies 0001_seed_pricing.sql on first run
        record(state_yaml_path, payload, db=db)  # db may be None in test/offline mode
    finally:
        if db is not None:
            db.close()
```

`record()` accepts an optional `db` and passes it to `_compute_cost_usd(db, agent, usage)`. When `db is None`, `_compute_cost_usd` returns `(resolved_model_id, None)` with a stderr warning — mirroring today's fail-open behaviour when pricing is unresolvable. This keeps `usage.get("cost_usd")` present-when-resolvable in the state.yaml step_history, preserving the existing contract tested by `test_record_cost_compute.py`.

**Why B' over A (move to `_record_main`) and C (open-inside-_compute_cost_usd):**

- **A rejected**: `_record_main` at `bin/orchestrator:565` only opens `_db` on the `next` dispatch branch, not the `record` branch (line 528–530 delegates straight to `record.main()` and exits). `cost_usd` is written to state.yaml step_history by `record()`; moving compute to `_record_main` would break `test_record_cost_compute.py::test_preserves_existing_cost_usd` et al. and strip cost from the state.yaml contract.
- **C rejected**: opening a second DuckDB handle inside `_compute_cost_usd` collides with DuckDB's single-writer lock in `_ingest_subagents_main` (which already holds an RW `_db`). Also adds a new import and test-fixture branching.
- **B' accepted**: cost computation stays in `record.py`; exactly one DB connection per `record.main()` invocation; zero ripple into test signatures (tests continue to call `record()` directly — `db=None` falls back cleanly); `_ingest_subagents_main` reuses its existing handle; `_ingest_driver_main` only reorders two lines.

Lookup SQL (unchanged):

Lookup SQL:

```sql
SELECT input_usd, output_usd, cache_read_usd, cache_creation_usd
FROM pricing
WHERE model_id = ? AND effective_from <= ?
ORDER BY effective_from DESC
LIMIT 1
```

Algorithm:
1. Resolve `model_id` from `usage.model` first (billing truth), else via routes.yaml (unchanged).
2. Run the SELECT above with `(model_id, now or datetime.utcnow())`.
3. If zero rows, re-run with `model_id='__default__'` at the same `now`.
4. If still zero rows, stderr-warn and return `(model_id, None)` — means the migration did not apply (operational error, not business error).
5. Compute cost from the returned rates using the existing arithmetic.

The `@lru_cache` `_load_pricing` is deleted. `_load_routes` remains (routes are still YAML).

#### 5. `config/scripts/orchestrator_next/cost_report.py` (modify `_load_pricing_for_model`)

```python
def _load_pricing_for_model(db, model: str | None) -> dict:
    """Read rates for `model` from `pricing`, falling back to __default__, then to a
    built-in conservative dict if the pricing table is absent (e.g. read-only open
    on an un-migrated DB)."""
    fallback = {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_creation": 18.75}
    try:
        row = db.execute(
            "SELECT input_usd, output_usd, cache_read_usd, cache_creation_usd "
            "FROM pricing WHERE model_id = ? "
            "ORDER BY effective_from DESC LIMIT 1",
            [model or "__default__"],
        ).fetchone()
        if row is None and model:
            row = db.execute(
                "SELECT input_usd, output_usd, cache_read_usd, cache_creation_usd "
                "FROM pricing WHERE model_id = '__default__' "
                "ORDER BY effective_from DESC LIMIT 1"
            ).fetchone()
    except duckdb.Error:
        return fallback
    if row is None:
        return fallback
    return {
        "input": row[0] or 0,
        "output": row[1] or 0,
        "cache_read": row[2] or 0,
        "cache_creation": row[3] or 0,
    }

# NOTE: gross_usd intentionally uses latest rates (no effective_from <= ts filter).
# TODO(phase-N): switch to per-event temporal lookup when historical dashboards are ready.
```

The `try/except duckdb.Error` is the read-only safety net for AC-7.

#### 6. `config/scripts/orchestrator_next/jsonl_usage.py` — NO CHANGE

Locked decision (OQ-3): preserve the existing double-entry strategy from `pricing.yaml` in the DB seed. The seed migration already emits explicit rows for `[1m]` and date-suffixed variants when they appear. `jsonl_usage.py` continues to pass the model string through verbatim; `_compute_cost_usd` continues to look up the exact string.

Rationale: zero hot-path logic change. If a new variant appears in JSONL, add a row via `scripts/ingest-pricing.py` — data change, not code change. This avoids a regex in the ingestion path that would need to keep pace with Anthropic's naming convention.

#### 7. `scripts/ingest-pricing.py` (new)

```python
#!/usr/bin/env python3
"""Insert a new pricing row with a fresh effective_from.

Usage:
  scripts/ingest-pricing.py \
    --model claude-haiku-5-0 \
    --input-usd 1.00 --output-usd 5.00 \
    --cache-read-usd 0.10 --cache-creation-usd 1.25 \
    [--is-local] [--effective-from 2026-06-01T00:00:00] \
    [--db ~/.state/orchestrator.duckdb]

On success: prints `inserted <model> @ <effective_from>`, exits 0.
On error: prints to stderr, exits non-zero.
"""
```

Argparse with type=float for the rate fields (validation); `--effective-from` defaults to UTC now. Opens the DB RW, calls `ensure_schema` (so the target table is guaranteed), INSERTs via a parameterised statement, commits.

**Import path**: `scripts/ingest-pricing.py` lives outside `config/scripts/orchestrator_next/`, so `from orchestrator_next.upsert import ensure_schema` does not resolve by default. Mirror the pattern already used by `scripts/inline/ingest-feature-metrics.py` (lines 33–37): resolve `ORCHESTRATOR_HOME` (env var, else the parent of the script's directory), then `sys.path.insert(0, os.path.join(ORCHESTRATOR_HOME, "config", "scripts"))` BEFORE the `from orchestrator_next...` import. A `python scripts/ingest-pricing.py --help` invocation with and without `ORCHESTRATOR_HOME` set must both resolve the import without error — T-12 Verify asserts this.

Why Python, not bash: matches the `scripts/inline/ingest-feature-metrics.py` precedent; typed arg parsing and parameterised INSERT are trivial in Python and fragile in bash.

#### 8. `config/scripts/estimate-cost.sh` (modify `lookup_pricing`)

Replace the AWK block with:

```bash
DB="${ORCHESTRATOR_DB:-$HOME/.state/orchestrator.duckdb}"  # canonical path TBD in env

lookup_pricing() {
  local model="$1"
  # Exact SQL shape, parameterisation via duckdb CLI parameter syntax.
  if [[ -f "$DB" ]]; then
    local row
    row=$(duckdb -readonly -json "$DB" \
      "SELECT input_usd, output_usd, cache_read_usd FROM pricing
         WHERE model_id = '${model//\'/\'\'}'
         ORDER BY effective_from DESC LIMIT 1" 2>/dev/null)
    if [[ -n "$row" && "$row" != "[]" ]]; then
      echo "$row" | python3 -c \
        'import json,sys;r=json.load(sys.stdin)[0];print(r["input_usd"],r["output_usd"],r["cache_read_usd"])'
      return
    fi
  fi
  # DB absent OR model missing — conservative default.
  echo "15.00 75.00 1.50"
}
```

Single-quote escaping is belt-and-braces (`duckdb` CLI does not support `?` placeholders — the pricing model-id strings are not user-controlled because they come from routes.yaml, but we still escape). If a future phase needs real parameterisation, swap to `duckdb -c ".mode json" -c "PREPARE q AS …"` — out of scope for phase 1.

#### 9. Tests

- `tests/test_migrations.py`: fresh DB → runner applies 0001 and records it; second invocation → no-op; adding a dummy `0002_noop.sql` → runner applies only 0002; a broken SQL file → raises and `schema_migrations` unchanged.
- `tests/test_pricing_lookup.py`: exact hit; unknown model → `__default__`; multiple rows for one model → latest `effective_from <= ts` wins; `is_local=TRUE` row round-trips.
- `tests/test_record_cost_compute.py` (modified): replace `monkeypatch.setenv(ORCHESTRATOR_HOME, …)` + `_load_pricing.cache_clear()` with an `in_memory_db` fixture that runs `ensure_schema(db)` and passes the connection into `_compute_cost_usd`.
- `tests/test_totals_wide.py` (modified): same fixture shape; drop direct YAML-rate imports.
- `scripts/tests/test_ingest_pricing.py`: CLI invocation against a temp DB; assert row insert; assert negative-rate rejection.
- `scripts/inline/tests/test_estimate_cost_sh.*`: seed a temp DuckDB, run the script, assert pricing section of output matches captured fixture byte-for-byte. Also run with `ORCHESTRATOR_DB=/nonexistent` to hit the default-rate fallback branch.

### Data Flow

**Write path (record):**
1. `record.main()` opens DuckDB RW.
2. Calls `ensure_schema(db)` → legacy ALTERs run → `_run_migrations(db)` runs → pricing seeded on fresh DB, no-op on migrated DB.
3. Parses stdin JSON → `usage` dict.
4. Calls `_compute_cost_usd(db, agent, usage)` → SQL lookup → returns `(model_id, cost_usd)`.
5. `upsert_step_event(db, …)` persists the row.

**Read-only paths (cost, metrics):**
1. `_cost_main` opens `read_only=True`. Does NOT call `ensure_schema`.
2. `cost_report.aggregate_feature(db, …)` → `_load_pricing_for_model(db, model)` → SQL, with `try/except duckdb.Error` returning the conservative fallback if the table is absent.

**Estimate path:**
1. `estimate-cost.sh` invokes `duckdb -readonly -json $DB "SELECT …"` per model.
2. JSON parsed by an inline `python3 -c '…'` one-liner (Python is already a project dep).
3. If DB file absent → default rates.

**Ingestion path:**
1. Operator runs `scripts/ingest-pricing.py --model … --effective-from …`.
2. Script opens DB RW, `ensure_schema(db)`, parameterised INSERT, commit.

### State Management

- `schema_migrations` rows accumulate forever (one per applied migration). This is the canonical audit trail.
- `pricing` rows accumulate over time (new `effective_from` per price change). No deletes, no updates.
- No in-memory caches on the SQL lookup path (a DuckDB in-process SELECT on a 10-row table is faster than the `@lru_cache` dict-return it replaces — see NFR-1 benchmark).

### Error Handling

| Failure | Behaviour |
|---|---|
| `ensure_schema` invoked on RO connection | DuckDB raises `IOException` — caller's responsibility, unchanged. |
| Seed SQL syntax error | Runner re-raises; `schema_migrations` row NOT inserted — safe to rerun after fix. |
| Unknown model_id in `_compute_cost_usd` | Falls back to `__default__` row, stderr warning. |
| Pricing table missing on RO cost_report path | `try/except duckdb.Error` → built-in fallback dict; no raise (AC-7). |
| `duckdb` CLI missing in PATH for estimate-cost.sh | `lookup_pricing` path fails; script falls through to `"15.00 75.00 1.50"` default — preview still renders. |
| `ingest-pricing.py` duplicate `(model_id, effective_from)` | Primary-key violation raised; script exits non-zero with a clear message; no partial state. Documented in script docstring. |

## Constraints

- No new CLI subcommands (two-verb end-state).
- No new runtime Python dependencies (duckdb, yaml, argparse already in use).
- SQL must be parameterised or escape-validated — mirror `upsert.py`.
- `estimate-cost.sh` output format MUST be byte-identical for equivalent inputs (fixture-parity test in AC-6).
- `_compute_cost_usd` signature changes from `(agent, usage)` → `(db, agent, usage)`; the single caller in `record.main()` is updated in the same task.

## Trade-offs

- **Schema evolution is forward-only.** No down-migrations. Accepted because DuckDB files in this project are not shared across conflicting code versions (each repo checkout owns its DB), and the team's existing pattern (`_migrate_step_events`) is also forward-only.
- **`gross_usd` stays latest-rates.** Accepted for phase 1 because changing historical dashboard numbers is a user-facing surprise. Marked with a `TODO(phase-N)` in code.
- **Bash consumer uses naive string-escape instead of real parameterisation.** Accepted because the `duckdb` CLI has no `?` parameter syntax and all inputs are non-user-controlled (model strings from routes.yaml). A future phase with user-controlled SQL would need a different path.
- **Two inline Python ALTER helpers (`_migrate_step_events`, `_migrate_tool_calls`) remain outside the runner.** Accepted because retrofitting them risks double-ALTER on existing DBs; new schema evolution from phase 2 onward goes through the runner.
- **`record.main()` opens a DuckDB connection per invocation (previously it opened none).** Accepted because the alternatives (moving compute downstream to `_record_main`, or opening inside `_compute_cost_usd`) either break the existing state.yaml cost_usd contract tested by `test_record_cost_compute.py` or risk concurrent DuckDB handles in `_ingest_subagents_main`. Connection overhead on an embedded in-process DuckDB is sub-millisecond and is dominated by the existing YAML I/O of `record()`. When no DB path resolves (test/offline — neither `METRICS_DB` nor `ORCHESTRATOR_HOME` set), `record.main()` passes `db=None` and `_compute_cost_usd` falls back to default rates with a stderr warning — preserving today's fail-open behaviour.

## Decisions

- Custom runner over yoyo-migrations → zero runtime deps, matches existing `upsert.py` pattern → ~40 line addition, no supply-chain risk.
- Standalone `scripts/ingest-pricing.py` over "just add another migration file" → separates data changes (frequent) from schema changes (rare) → clean audit: `schema_migrations` is DDL history, `pricing` rows are rate history.
- `estimate-cost.sh` rewrites to `duckdb -json` over new CLI verb → honours the two-verb end-state constraint of the parent effort → one script change, zero CLI surface growth.
- `[1m]` and date-suffix variants kept as double-entry in seed → zero hot-path logic change; the existing YAML already does this → any future variant is a data change (ingestion script), not a code change.
- Runner lives inline in `upsert.py` as a sibling to `_migrate_step_events` / `_migrate_tool_calls` → follows existing repo pattern, one import site, no new module surface → phase 2+ additions add SQL files, not Python modules.
- `try/except duckdb.Error` fallback in `cost_report._load_pricing_for_model` → `orchestrator cost` / `metrics` keep working on DBs predating the pricing migration → AC-7 regression safety.
- Legacy `_migrate_*` helpers NOT retrofitted → retrofitting risks double-ALTER on existing DBs → explicit carve-out documented here.

## Open Questions

None. OQ-1 through OQ-6 from discovery.md are resolved in spec.md § Decisions and design.md § Decisions.
