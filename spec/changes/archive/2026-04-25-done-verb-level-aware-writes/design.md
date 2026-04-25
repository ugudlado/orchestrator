# Design: Done verb + level-aware writes (Phase 4)

## Context

The orchestrator's metrics pipeline currently writes through three CLI verbs:

- `orchestrator record` (step_events upsert, called once per step by drivers)
- `orchestrator ingest-driver` (driver_sessions equivalent, run as a fail-soft inline step at end of complete phase)
- `orchestrator ingest-subagents` (extends step_events with subagent token counts, also fail-soft)

The two ingest verbs are silently skipped on environment differences (missing JSONL, session_id resolution failure), producing partial DuckDB rows. The Phase 4 backlog mandates: rename `record` → `done`, add level-aware writes (`done` writes phase + feature rows internally on boundary detection), and absorb the two ingest verbs.

The design must navigate one critical constraint: **the workflow running this feature uses `orchestrator record` to advance its own steps**. A naive rename breaks the workflow mid-flight.

The DuckDB schema currently has `step_events`, `tool_calls`, `feature_complexity`, `feature_metrics`, plus views `feature_report` / `phase_report` / `agent_report` / `repo_report` (Phase 3). The new tables must complement these — specifically, `phase_report` is currently a view aggregating `step_events`. After Phase 4, `phase_events` is a populated table; the view either reads from it or continues to aggregate `step_events`. We keep `phase_report` reading `step_events` (no view change in Phase 4) — the new `phase_events` table is for future "phase-level facts" that don't aggregate cleanly from rows (e.g., wall-clock vs sum-of-steps duration).

## Goals / Non-Goals

### Goals

- One CLI verb (`done`) covers all step outcomes and triggers level-aware writes.
- Boundary detection adds zero new file-system reads (uses in-memory `workflow_plan`).
- Phase + feature boundary writes are atomic with the step write.
- Bootstrap-safe migration: workflow stays runnable at every commit.
- Module name stays `record.py` to keep import surface stable.

### Non-Goals

- Renaming the Python module `record.py` → `done.py`.
- Rewriting `phase_report` view to read from new `phase_events` table.
- Touching `feature_metrics` / `ingest-feature-metrics` (Phase 5).
- Backfilling historical DuckDB data into the new tables.
- UI / dashboard changes.

## Approaches Considered

### Approach 1: Atomic rename + level-aware writes in one pass (M complexity)

Rename `record` → `done`, update all 10+ callers, add tables, implement boundary logic — single PR.

Pros: Clean diff, no intermediate alias state.
Cons: Bootstrap-hazardous. Any caller missed mid-rename breaks the running workflow. Test coverage of the migration cannot recover from mid-rename failure of the workflow itself. Single-shot coordination across `bin/orchestrator`, SKILL.md, agent .md files, CLAUDE.md, step contracts, gate scripts, prose tests.

Module reuse: 1 (extends `record.py`).
Complexity: **M** (numeric 3).

### Approach 2: Staged alias migration (S complexity, recommended)

Three sub-steps shipped sequentially:

- Stage A: Add `done` as a new verb in `bin/orchestrator` dispatching to the existing `record_main`. Add migration `0003`. Implement `payload.status` dispatch and boundary detection inside `record()`. Both verbs work; the workflow continues to call `record`.
- Stage B: Migrate every production caller (SKILL.md, agents, CLAUDE.md, step contracts) from `record` to `done`. Update `m8-gates.sh` and `test_prose_contracts.py`. Both verbs still work.
- Stage C: Remove `record` from the usage banner and remove `ingest-driver`/`ingest-subagents` verbs/inline scripts. `record` continues to silently route to `done` for one cycle as in-flight workflow protection.

Pros: Each stage is small and independently verifiable. Bootstrap hazard neutralized — `record` stays live throughout the migration. Caller migration is a pure search-and-replace, separable from the boundary-write logic. Three small commits instead of one risky large one.
Cons: Slightly more code in Stage A (alias routing layer, ~3 lines). `record` lives one cycle longer than necessary.

Module reuse: 1 (extends `record.py`); same as Approach 1.
Complexity: **S** (numeric 2). Each stage is XS-S; the cumulative scope sits at S because the work is sequenced, not concurrent.

### Approach 3: Permanent dual verbs (XS complexity, but rejected on goal grounds)

Keep `record` forever; add `done` as the new richer verb; let callers migrate organically.

Pros: Smallest implementation; zero migration risk.
Cons: Two verbs with overlapping semantics permanently contradicts the backlog goal of consolidation. Drivers would still have to know which verb does what. The whole point of Phase 4 is one-verb-rules-them-all.

Module reuse: 1.
Complexity: **XS** (numeric 1).

### Selected Approach

**Approach 2: Staged alias migration.**

Auto-selection heuristic application:

| Approach | Complexity (numeric) | Module reuse | Goal-aligned? |
|----------|----------------------|--------------|---------------|
| 3        | 1 (XS)               | 1            | NO (dual verbs forever) |
| 2        | 2 (S)                | 1            | YES |
| 1        | 3 (M)                | 1            | YES |

Approach 3 has the lowest numeric complexity but is goal-disqualified — it does not satisfy FR-2/FR-7/FR-8 (consolidation, ingest-verb removal). The auto-selection heuristic applies among approaches that satisfy the goal. Among Approaches 1 and 2, the lower numeric complexity wins: Approach 2 (S=2) over Approach 1 (M=3). Bootstrap safety is the additional reason Approach 1 is unacceptable.

## High-Level Design

### Architecture Overview

```
                ┌─────────────────────────────────────┐
                │  Driver (skill / agent / inline)    │
                └──────────────┬──────────────────────┘
                               │ orchestrator done state.yaml <<< {payload}
                               ▼
                ┌─────────────────────────────────────┐
                │  bin/orchestrator                    │
                │   verb dispatch: 'done' or 'record'  │
                │   → orchestrator_next.record.main    │
                └──────────────┬──────────────────────┘
                               ▼
                ┌─────────────────────────────────────┐
                │  record.main(argv)                   │
                │   - parse JSON payload from stdin    │
                │   - open DuckDB, ensure_schema       │
                │   - call record(state_yaml, payload) │
                └──────────────┬──────────────────────┘
                               ▼
       ┌───────────────────────┴────────────────────────────┐
       │  record(state_yaml_path, payload, db)               │
       │                                                      │
       │   1. validate payload, slug, status                  │
       │   2. status dispatch:                                │
       │       completed → step write + boundary check       │
       │       recovered → step write only (status=recovered)│
       │       abandoned → step write + state.status=blocked │
       │                                                      │
       │   3. boundary check (only on 'completed'):           │
       │       phase_boundary  = step_id == active[-1]        │
       │       feature_boundary = phase_boundary              │
       │                          AND phase == last phase     │
       │                                                      │
       │   4. write strategy:                                 │
       │       no boundary → upsert_step_event (fail-soft)    │
       │       phase boundary → BEGIN; step + phase; COMMIT   │
       │       feature boundary → BEGIN; step + phase + drv;  │
       │                          COMMIT                      │
       └──────────────┬───────────────────────────────────────┘
                      ▼
            ┌──────────────────────┐
            │  DuckDB metrics.duckdb│
            │   step_events         │
            │   phase_events  (NEW) │
            │   driver_sessions(NEW)│
            └───────────────────────┘
```

### Key Abstractions

- **`payload.status`**: enumeration of `completed | recovered | abandoned`. Replaces today's implicit single-status assumption. Default if absent for backward compat: `completed`.
- **Boundary**: a property of `(workflow_plan, current_phase, current_step_id)`. Computed by a small pure function `_detect_boundary(workflow_plan, phase, step_id) → BoundaryKind` where `BoundaryKind ∈ {NONE, PHASE, FEATURE}`.
- **Atomic write**: a context manager wrapping `db.execute("BEGIN")` / `db.execute("COMMIT")` / `db.execute("ROLLBACK")` around all writes triggered by one `done` call.
- **Driver session resolution**: `_resolve_driver_session(state, change_id)` returns `(session_id, total_tokens, cost_usd, model, started_at, ended_at)` by inspecting `$ORCHESTRATOR_DRIVER_SESSION_ID` env var first, then scanning the most recent JSONL session log under `$HOME/.claude/projects/<encoded_repo>/`. This logic is lifted from `bin/orchestrator:_ingest_driver_main` (lines 53-138).
- **Subagent row resolution**: `_resolve_subagent_rows(repo_root, change_id, session_id)` returns a list of `(agent_name, step_id, usage)` tuples — one per sub-agent JSONL discovered under `~/.claude/projects/<slug>/<session>/subagents/`. This logic is lifted from `bin/orchestrator:_ingest_subagents_main` (lines 140-265). Per-row construction is fail-soft (one bad transcript or missing meta.json skips that row, logged to stderr) but discovery + parse run OUTSIDE the boundary transaction so the BEGIN/COMMIT window stays small. Companion `_write_subagent_events(db, repo_root, change_id, rows)` performs the inserts INSIDE the transaction via `upsert_synthetic_event`, with the legacy idempotency check (skip if existing row for the (repo, change_id, phase='meta', step_id, attempt=1) PK already has non-zero `input_tokens`).

## Low-Level Design

### Components

**1. `bin/orchestrator` — verb dispatch**

Stage A change (additive):
```python
# Line 334 (current): if not args or args[0] not in ("next", "record", "doctor", "ingest-driver", "ingest-subagents"):
# Stage A:
if not args or args[0] not in ("next", "done", "record", "doctor", "ingest-driver", "ingest-subagents"):
    ...
if len(args) < 2 and args[0] in ("next", "record", "done"):
    ...
if args[0] in ("record", "done"):
    from orchestrator_next.record import main as record_main
    sys.exit(record_main(sys.argv[1:]))
```

Stage C change (subtractive):
```python
# accepted verbs become: ("next", "done", "record", "doctor")
# usage banner shows only `orchestrator done <state.yaml>` line
# _ingest_driver_main and _ingest_subagents_main functions are removed
```

The `record` verb stays accepted in Stage C for one cycle (in-flight workflows that started before Stage B caller-migration may still call `record`). It does not appear in the banner.

**2. `orchestrator_next/record.py` — status dispatch + boundary detection**

New helpers (pure functions where possible, side-effecting clearly named):

```python
class BoundaryKind(str, Enum):
    NONE = "none"
    PHASE = "phase"
    FEATURE = "feature"

def _detect_boundary(workflow_plan: dict, phase: str, step_id: str, status: str) -> BoundaryKind:
    """Returns BoundaryKind based on workflow_plan and current step.
    Returns NONE for any status != 'completed'."""
    if status != "completed":
        return BoundaryKind.NONE
    phase_block = workflow_plan.get(phase) or {}
    active = phase_block.get("active") or []
    if not active or step_id != active[-1]:
        return BoundaryKind.NONE
    # phase_id is the last entry — at minimum a phase boundary
    phase_keys = list(workflow_plan.keys())
    if phase_keys and phase == phase_keys[-1]:
        return BoundaryKind.FEATURE
    return BoundaryKind.PHASE

def _write_phase_event(db, repo_root, change_id, phase, attempt) -> None:
    """Insert a phase_events row aggregated from step_events. Caller is
    responsible for transaction control."""
    # SELECT aggregates and INSERT — see SQL below

def _resolve_driver_session(state, change_id) -> dict:
    """Lift logic from bin/orchestrator:_ingest_driver_main. Returns dict
    with session_id, total_tokens, cost_usd, model, started_at, ended_at.
    Raises if session_id cannot be resolved."""

def _write_driver_session(db, repo_root, change_id, session) -> None:
    """Insert a driver_sessions row. Caller controls transaction."""

def _resolve_subagent_rows(repo_root: str, change_id: str, session_id: str) -> list[dict]:
    """Discover sub-agent JSONLs and parse usage. Returns a list of dicts:
        {"agent_id", "agent_name", "step_id", "phase", "usage"}
    Per-row failures (missing meta.json, malformed JSONL, no usable turns) skip
    that row with a stderr log; the function never raises on per-row issues.
    Imports `discover_subagents`, `extract_agent_usage`, `locate_subagent_jsonl_path`
    from `orchestrator_next.jsonl_usage` (already used by the legacy ingest).
    Runs OUTSIDE the boundary transaction.
    """

def _write_subagent_events(db, repo_root, change_id, rows) -> None:
    """Insert one synthetic step_events row per tuple via `upsert_synthetic_event`.
    Computes `cost_usd` via `_compute_cost_usd` per row before insert. Honors the
    legacy idempotency check: skip if a row already exists for
    (repo_root, change_id, phase='meta', step_id, attempt=1) with non-zero
    input_tokens. Caller controls the transaction. Per-row insert errors are
    logged to stderr but do not raise — consistent with the discovery pass."""
```

Modified `record()` flow (around current line 602, the `response = {"action": "recorded", ...}` block):

```python
status = payload.get("status", "completed")
boundary = _detect_boundary(state["workflow_plan"], phase, step_id, status)

# Status-dependent state.yaml mutations
if status == "abandoned":
    state["status"] = "blocked"

# DuckDB writes
if db is not None:
    if boundary == BoundaryKind.NONE:
        # current fail-soft path
        try:
            upsert_step_event(db, entry, ctx)
        except Exception as exc:
            sys.stderr.write(f"[done] step write failed: {exc}\n")
    else:
        # FEATURE boundary: pre-resolve subagent rows OUTSIDE the transaction
        # so JSONL parsing does not hold the BEGIN/COMMIT window open.
        subagent_rows = []
        session = None
        if boundary == BoundaryKind.FEATURE:
            session = _resolve_driver_session(state, change_id)  # may raise → fatal
            subagent_rows = _resolve_subagent_rows(
                repo_root, change_id, session["session_id"]
            )  # fail-soft per row; never raises

        # atomic boundary write — fatal on failure
        db.execute("BEGIN")
        try:
            upsert_step_event(db, entry, ctx)
            _write_phase_event(db, repo_root, change_id, phase, entry["attempt"])
            if boundary == BoundaryKind.FEATURE:
                _write_driver_session(db, repo_root, change_id, session)
                _write_subagent_events(db, repo_root, change_id, subagent_rows)
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise  # fatal — non-zero exit
```

Update CLI usage string in `main()`:
```python
print("Usage: orchestrator done <state.yaml>  (JSON payload on stdin)", file=sys.stderr)
```

**3. `orchestrator_next/migrations/0003_phase_events_driver_sessions.sql` — DDL**

Style mirrors `0001_seed_pricing.sql` (NOT NULL columns where appropriate, PRIMARY KEY clause, IF NOT EXISTS for safety even though the migration runner already gates on `schema_migrations`). Column types and aggregate names align with the existing `step_events` and `phase_report` view shapes so no view changes are forced.

```sql
-- Phase 4 of workflow-engine-as-state-machine.
-- Adds phase_events and driver_sessions tables for level-aware writes from `orchestrator done`.

CREATE TABLE IF NOT EXISTS phase_events (
  repo_root         VARCHAR NOT NULL,
  change_id         VARCHAR NOT NULL,
  phase             VARCHAR NOT NULL,
  attempt           INTEGER NOT NULL,
  step_count        INTEGER NOT NULL,
  cost_usd          DOUBLE  NOT NULL DEFAULT 0.0,
  input_tokens      BIGINT  NOT NULL DEFAULT 0,
  output_tokens     BIGINT  NOT NULL DEFAULT 0,
  cache_read_input_tokens     BIGINT NOT NULL DEFAULT 0,
  cache_creation_input_tokens BIGINT NOT NULL DEFAULT 0,
  duration_ms       BIGINT  NOT NULL DEFAULT 0,
  started_at        TIMESTAMP,
  ended_at          TIMESTAMP,
  upserted_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (repo_root, change_id, phase, attempt)
);

CREATE INDEX IF NOT EXISTS idx_phase_events_change
  ON phase_events(repo_root, change_id);

CREATE TABLE IF NOT EXISTS driver_sessions (
  repo_root         VARCHAR NOT NULL,
  change_id         VARCHAR NOT NULL,
  session_id        VARCHAR NOT NULL,
  model             VARCHAR,
  total_tokens      BIGINT  NOT NULL DEFAULT 0,
  input_tokens      BIGINT  NOT NULL DEFAULT 0,
  output_tokens     BIGINT  NOT NULL DEFAULT 0,
  cost_usd          DOUBLE  NOT NULL DEFAULT 0.0,
  started_at        TIMESTAMP,
  ended_at          TIMESTAMP,
  upserted_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (repo_root, change_id, session_id)
);

CREATE INDEX IF NOT EXISTS idx_driver_sessions_change
  ON driver_sessions(repo_root, change_id);
```

The phase_events column names match those used by the existing `phase_report` view (verified against `0002_report_views.sql:234-247`) so a future view rewrite to read from the table is mechanical. SQL field-name validation: column names will be re-verified against the live schema after migration runs in T-3 / T-4 verification (see Decisions § "SQL field-name validation").

**4. `_resolve_driver_session()` — session-id resolution**

Lifted from `bin/orchestrator:_ingest_driver_main` (lines 53-137):

1. Read `$ORCHESTRATOR_DRIVER_SESSION_ID` from environment.
2. If absent: scan `$HOME/.claude/projects/<encoded_repo>/` for the most recent JSONL file by mtime; treat its filename stem as the session_id.
3. If still absent: raise `RuntimeError("driver session_id not resolvable")`.
4. Aggregate token counts from the JSONL file (`message.usage` blocks), compute `cost_usd` via `_compute_cost_usd`, return dict.

Failure mode: at the feature boundary, if `_resolve_driver_session` raises, the BEGIN/COMMIT block ROLLBACKs (atomic guarantee) and the call exits non-zero. The driver retries the step or escalates to `doctor`. This is intentional: silent driver_sessions loss is the bug Phase 4 fixes; surfacing it loudly is the value prop.

### Data Flow

1. Driver completes a step → calls `orchestrator done state.yaml <<< {payload}`.
2. `bin/orchestrator` dispatches to `record_main(argv)`.
3. `record_main` parses JSON, opens DuckDB, calls `record(state_yaml_path, payload, db)`.
4. `record` validates, mutates `state.yaml.step_history`, writes file.
5. `_detect_boundary` classifies the call.
6. Conditional DuckDB write path:
   - NONE → fail-soft `upsert_step_event`.
   - PHASE → BEGIN → step + phase → COMMIT.
   - FEATURE → BEGIN → step + phase + driver_session → COMMIT.
7. `record` returns `(response, code)`. Response includes `boundary: phase|feature|none` so drivers can log the event.
8. `record_main` prints JSON, exits with code.

### State Management

- `state.yaml.step_history` — appended on every call (current behavior preserved).
- `state.yaml.status` — set to `blocked` when `payload.status == abandoned`.
- DuckDB `step_events` — upserted on every call (current behavior preserved when no boundary).
- DuckDB `phase_events` — inserted on phase or feature boundary.
- DuckDB `driver_sessions` — inserted on feature boundary only.

### Error Handling

| Error | Behavior |
|-------|----------|
| Missing or invalid JSON payload | Exit 3 (current behavior preserved). |
| Invalid slug `change_id` | Exit non-zero, current validation preserved. |
| DuckDB unavailable (file not found, can't open) | Step write fail-soft on non-boundary calls (current behavior). On boundary calls: fatal — boundary writes require DB. |
| `step_events` upsert fails on non-boundary call | Stderr log, exit 0 (current fail-soft preserved). |
| Boundary write fails (any of step / phase / driver) | ROLLBACK, exit non-zero. The driver retries or escalates. |
| `_resolve_driver_session` cannot resolve session_id | Raises inside BEGIN block → ROLLBACK → exit non-zero. |
| `payload.status` not in `{completed, recovered, abandoned}` | Exit 3 with clear error. Default to `completed` only when key is absent. |
| In-flight workflow calls `record` after Stage B caller migration | Routes silently to `done` (Stage C accepted-verb tuple keeps `record`). |

## Constraints

- DuckDB transactions: BEGIN/COMMIT/ROLLBACK must be issued via `db.execute()`. No nested transactions.
- All new SQL parameterised. No string interpolation.
- `change_id` slug validation re-applied before any new INSERT.
- Module name `record.py` is fixed by Decision (not renamed in Phase 4).
- New tables must coexist with existing `phase_report` view, which reads from `step_events`. Column names chosen to align so a future view rewrite is mechanical.
- Migration runner is in `upsert.py:_run_migrations`; `0003` placement under `migrations/` is auto-discovered in lexical order.

## Trade-offs

- **Module name vs. semantic consistency**: keeping `record.py` while adding `done` verb leaves a name mismatch. Accepted because the import-surface churn (6 test files + bin/orchestrator) is not worth the cosmetic gain. A rename can be a future cleanup.
- **Fatal vs. fail-soft for boundary writes**: chose fatal. Costs availability (a DuckDB lock now fails the step), buys consistency (no silent partial state). Phase 4's value proposition is the consistency guarantee.
- **Stage C `record` removal timing**: kept silently routing for one cycle. Costs banner clarity (banner says only `done`), buys in-flight workflow safety (workflows mid-flight calling `record` keep working).
- **Session-id resolution inside the boundary transaction**: if JSONL scanning is slow (large session log), it extends the transaction window. Acceptable because the feature boundary fires once per workflow and the JSONL is append-only on the local filesystem (no network). Observed historical scans complete in well under 100 ms.
- **Subagent absorption — Option A (absorb) over Option B (drop)**: Phase 4 absorbs `_ingest_subagents_main` into `_resolve_subagent_rows` + `_write_subagent_events`. Costs implementation surface (two helpers + tests + transaction-window length when N subagents is large). Buys: (1) preserves the `agent_report` view's per-subagent attribution rows (these are step-level fan-out, not aggregable from feature-level driver totals); (2) honors the parent backlog scope ("Absorbs `ingest-driver` / `ingest-subagents` as internal code paths called from `done`"); (3) keeps Phase 5's deletion of CLI entry points clean — Phase 5 has nothing to delete cleanly if the subagent path was silently dropped here. Option B (drop) was rejected because boundary detection alone does not recover per-subagent rows; the regression would surface in `agent_report` and break Phase 5's contract.
- **Subagent JSONL parse outside the transaction**: `_resolve_subagent_rows` performs all JSONL discovery and parse work BEFORE `BEGIN`. Only the `upsert_synthetic_event` calls run inside the transaction. Costs: a small window between resolve and write where new subagent activity could appear (acceptable — subagents are spawned only during step execution, never between feature boundary detection and write). Buys: minimal transaction window even when many subagents exist.
- **Per-subagent fail-soft inside fatal boundary**: a malformed sub-agent JSONL or missing meta.json skips that one row (logged to stderr) without aborting the transaction. The transaction itself is still fatal-on-failure (consistent with OQ-2). This matches the legacy `_ingest_subagents_main` per-row try/except behavior — a single bad subagent shouldn't lose all the others.

## Decisions

- Keep Python module `record.py` (not renamed) → minimizes import surface churn → 6 test files + `bin/orchestrator:84,170` keep working unchanged.
- Boundary detection reads `state.yaml.workflow_plan[phase].active` → no new file IO, no path resolution → `_detect_boundary` is a pure function over already-parsed state.
- Boundary write is fatal on failure → consistency over availability → driver retry/escalate path activates instead of silent partial commit.
- Step write stays fail-soft on non-boundary calls → preserves current `record.py` behavior → no regression for the common case.
- `abandoned` writes the step row + sets `state.status: blocked`, no boundary trigger → downstream phase aggregation in `phase_report` filters via WHERE clause if needed → Phase 4 does not modify `phase_report` view.
- `recovered` writes the step row, no boundary trigger, no `state.status` change → recovery is a step-local event; the phase only closes via a real `completed` last-step → `phase_events` rows always represent successful phase closure.
- m8-gates banner formulation: Stage A interim accepts `done|record`; Stage C asserts `done` strict and asserts `record` is NOT present in banner → gate is meaningful at every stage transition without permitting trivial pass-through during interim.
- Phase 5 boundary explicit: `ingest-feature-metrics` step, `feature_metrics` table, and `upsert_feature_metrics()` function are NOT touched in Phase 4 → spec.md "Out of scope" makes this explicit so the implementer doesn't drift.
- Subagent absorption uses Option A (absorb into `_resolve_subagent_rows` + `_write_subagent_events`), not Option B (drop) → preserves `agent_report` per-subagent rows + honors parent backlog scope + keeps Phase 5 deletion clean → rationale recorded in Trade-offs above.
- Subagent discovery + JSONL parse run OUTSIDE the BEGIN/COMMIT window; only `upsert_synthetic_event` inserts run INSIDE → keeps transaction window short even when N subagents is large.
- SQL field-name validation: `phase_events` column names (`cost_usd`, `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `duration_ms`, `step_count`, `started_at`, `ended_at`) cross-checked against `step_events` DDL at `upsert.py:29-55` and `phase_report` view aggregate column names at `0002_report_views.sql:234-247`. Names match. T-4 task includes a one-query verification that runs `DESCRIBE phase_events` and asserts the column list.

## Open Questions

- (None blocking implementation. Stage A migration order, boundary detection mechanism, fatal-on-failure semantics, module-name decision, session-id resolution location, m8-gates formulation, and Phase 5 boundary are all closed in Decisions above.)
